#!/bin/bash
# The end-to-end test: a run of tests/mission with two agents. Once ada's first model call is in and her home holds her
# conversation, the test kills her worker. Once the server has started her again, the test kills the server, and
# locally starts it again on the same data. It passes once the restarted server has found both agents' workers without
# starting them again, ada and everyone have each made a model call since, ada's log shows her session resumed, her
# restarted worker still has her work tree, tools/score.py reports 1.0 on the run's export, the watch link shows the
# run, and no worker outlives the run (locally, her token no longer works once it is stopped either). It fails at the
# deadline (24 minutes).
#
#   tests/e2e.sh            its own local server, on a fresh data folder and a free port
#   tests/e2e.sh --modal    deploys the server, then a run on the Modal server `swarmory login` points at
#
# Model calls use the server's ANTHROPIC_API_KEY and OPENAI_API_KEY (locally, your shell's), or its
# CLAUDE_CODE_OAUTH_TOKEN and CODEX_AUTH_JSON logins when E2E_SUBSCRIPTION is set. The team is
# claude-code:claude-haiku-5-5=1 codex:gpt-6.1-sol=1; E2E_TEAM="codex:gpt-6.1-sol=2" replaces it.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"  # this checkout's swarmory, for the server, swarmory and tools/
modal=0
[ "${1:-}" = --modal ] && modal=1
extra=""
[ $modal = 1 ] && extra="--extra modal"  # deploy needs the Modal client
PY=$(uv run -q $extra python -c 'import sys; print(sys.executable)') || exit 1  # this checkout's swarmory
swarmory() { "$PY" -m swarmory.cli "$@"; }
say() { echo "e2e $(date +%H:%M:%S) $*"; }
args=(start tests/mission --hours 0.4)
[ -n "${E2E_SUBSCRIPTION:-}" ] && args+=(--subscription)
for t in ${E2E_TEAM:-claude-code:claude-haiku-5-5=1 codex:gpt-6.1-sol=1}; do args+=(--team "$t"); done
data=$(mktemp -d "${TMPDIR:-/tmp}/e2e.XXXXXX")  # locally the server's data; on Modal the export and logs
run="" server=""

login() {  # login url|token: from the login file, which this test never prints
  "$PY" -c "import json, os, sys
print(json.load(open(os.environ.get('SWARMORY_LOGIN') or os.path.expanduser('~/.swarmory/login')))[sys.argv[1]])" "$1"
}
up() {  # waits up to 5 minutes for the server to answer /health (a first start installs LiteLLM)
  local i
  for i in $(seq 150); do curl -sf "$(login url)/health" > /dev/null && return 0; sleep 2; done
  return 1
}
serve() {  # a local server on $data, in the background, with the token this test made
  SWARMORY_OPERATOR_TOKEN="$token" "$PY" -m swarmory.cli serve --data "$data" --port "$port" \
    >> "$data/server.log" 2>&1 &
  server=$!
}
if [ $modal = 1 ]; then
  swarmory deploy > "$data/deploy.log" 2>&1 || { cat "$data/deploy.log"; say "FAIL: deploy failed"; exit 1; }
  case "$(login url 2> /dev/null)" in
    https://*) ;;
    *) say "FAIL: log in to the Modal server first: swarmory login URL TOKEN"; exit 1 ;;
  esac
  up || { say "FAIL: the deployed server does not answer"; exit 1; }  # a deploy starts a new container
else
  export SWARMORY_LOGIN="$data/login"  # the person's own login stays as it is
  port=$("$PY" -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])")
  token=$("$PY" -c "import secrets; print(secrets.token_hex(16))")
  serve
  swarmory login "http://127.0.0.1:$port" "$token" > /dev/null
  up || { cat "$data/server.log"; say "FAIL: the server did not start"; exit 1; }
fi

modal_sandbox() {  # modal_sandbox ID CODE: runs CODE with `sb`, ada's or ID's agent Sandbox (tagged as modal.py does)
  uv run -q --with modal==1.6.3 python -c "import modal, sys
app = modal.App.lookup('swarmory-workers')
tags = {'run': sys.argv[1], 'role': 'agent', 'id': sys.argv[2]}
sb = next(iter(modal.Sandbox.list(app_id=app.app_id, tags=tags)))
$2" "$run" "$1" "${@:3}"
}
home() { echo "$data/runs/$run/members/ada"; }  # ada's home in a local run
work() {  # work CMD: runs CMD on ada's work tree's .git, where git never commits what it finds
  if [ $modal = 1 ]; then
    modal_sandbox ada "sys.exit(sb.exec('sh', '-c', 'cd /swarmory/work/.git && ' + sys.argv[3]).wait())" "$1"
  else (cd "$(home)/work/.git" && eval "$1"); fi
}
pid() { "$PY" -c "import json, sys; print(json.load(open(sys.argv[1]))['pid'])" "$1"; }  # pid FILE: a local worker's
status() { swarmory status "$run" 2> /dev/null; }
calls() {  # calls [ID] STATUS: model calls by ID, or by everyone: in each row, the column before "tokens in / out"
  awk -v id="${1:-}" 'id == "" || $1 == id {for (i = 3; i <= NF; i++) if ($i == "/") n += $(i - 2)}
    END {print n + 0}' <<< "$2"
}
starts() {  # starts ID STATUS: how often the server has started ID's worker, while it runs; else nothing
  awk -v id="$1" '$1 == id {for (i = 4; i < NF; i++) if ($i == "running") {print $(i + 1); exit}}' <<< "$2"
}
kill_ada() {  # as a crash would
  local g
  work "touch e2e-kept && sync ."  # looked for after her restart
  if [ $modal = 1 ]; then
    mark=$(resumed) && modal_sandbox ada "sb.terminate()"
  else
    g=$(pid "$data/workers/$run+agent+ada.json") && mark=$(wc -l < "$(home)/agent.log") && kill -9 -- "-$g"
  fi
}
kill_server() {  # as a crash would. Modal starts its one container again; locally the test does
  if [ $modal = 1 ]; then
    uvx modal container list --json | "$PY" -c "import json, sys
rows = json.load(sys.stdin)
pick = lambda r, *words: next((v for k, v in r.items() if all(w in k.lower() for w in words)), None)
print(*[pick(r, 'container', 'id') for r in rows if str(pick(r, 'app') or '') == 'swarmory'])" |
      xargs -n1 uvx modal container stop
  else
    kill -9 "$server" && wait "$server" 2> /dev/null
    serve
  fi
}
saved() {  # ada's home holds a conversation (a .jsonl), so her restart has one to resume
  if [ $modal = 1 ]; then
    modal_sandbox ada "sys.exit(sb.exec('sh', '-c', 'find /swarmory/home -name \"*.jsonl\" | grep -q .').wait())"
  else find "$(home)/home" -name '*.jsonl' 2> /dev/null | grep -q .; fi 2> /dev/null
}
resumed() {  # on Modal: how many sessions ada's log says she resumed (her new sandbox appends to the same log)
  modal_sandbox ada \
    "print(sb.exec('sh', '-c', 'grep -c \"started (resumed)\" /swarmory/agent.log').stdout.read().strip() or 0)"
}
restored() {  # ada's log since the kill: 1 her session resumed, 0 it started anew, "" no session yet
  if [ $modal = 1 ]; then
    [ "$(resumed 2> /dev/null || echo 0)" -gt "$mark" ] 2> /dev/null && echo 1
  else awk -v m="$mark" 'NR > m && /^session .* started/ {print (/resumed/) + 0; exit}' "$(home)/agent.log"; fi
}
watched() {  # the link `status` prints opens the run's log and ledger
  local link
  link=$(status | awk '$1 == "watch" {print $2}') && [ -n "$link" ] &&
    curl -sf -H "Authorization: Bearer ${link#*#}" "${link%/view#*}/feed?log=0&ledger=0" |
    "$PY" -c "import json, sys; d = json.load(sys.stdin); sys.exit(not (d['log'] and d['ledger']))"
}
score() {  # tools/score.py on the run's export
  rm -rf "${data:?}/export" &&
    swarmory export "$run" "$data/export" > /dev/null &&
    "$PY" tools/score.py tests/mission "$data/export/$run" 2> /dev/null | awk '/^score:/{s=$2} END{print s}'
}
cleanup() {
  [ -n "$run" ] && swarmory stop "$run" > /dev/null 2>&1
  [ -n "$server" ] && kill "$server" 2> /dev/null
}
trap cleanup EXIT

say "swarmory ${args[*]}"
swarmory "${args[@]}" > "$data/start.log" 2>&1 || { cat "$data/start.log"; say "FAIL: start failed"; exit 1; }
run=$(awk '/^run /{sub(":", "", $2); print $2; exit}' "$data/start.log")
[ -n "$run" ] || { cat "$data/start.log"; say "FAIL: no run in start's output"; exit 1; }
say "run $run (data: $data)"
deadline=$(($(date +%s) + 1440)) killed_ada=0 killed_server=0 score="" mark=0 ada=0 all=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 2
  st=$(status)
  if [ $killed_ada = 0 ]; then  # once her first model call is in and her home has her conversation
    ada=$(calls ada "$st")
    [ "$ada" -gt 0 ] && saved && say "killing ada after $ada calls" && kill_ada && killed_ada=1
    continue
  fi
  if [ $killed_server = 0 ]; then  # once the server has started ada again
    [ "$(starts ada "$st")" -ge 2 ] 2> /dev/null || continue
    before=$(awk '{print $1}' <<< "$st" | while read -r id; do
      s=$(starts "$id" "$st") && [ -n "$s" ] && echo "$id $s"
    done)
    all=$(calls "" "$st")
    say "killing the server after $all calls" && kill_server && killed_server=1
    up || { say "FAIL: the server did not come back"; break; }
    st=$(status)
    after=$(while read -r id s; do echo "$id $(starts "$id" "$st")"; done <<< "$before")
    [ "$before" = "$after" ] || { say "FAIL: the restarted server started running workers again: $after"; break; }
    continue
  fi
  # Score only once both are back and have worked since: the restarted ada has made a model call, and someone has
  # made one through the restarted server.
  r=$(restored)
  [ "$r" = 0 ] && tail -n "+$((mark + 1))" "$(home)/agent.log" && say "FAIL: ada did not resume" && break
  [ "$r" = 1 ] || continue
  work "test -f e2e-kept" || { say "FAIL: ada's restart lost her work tree"; break; }
  [ "$(calls ada "$st")" -gt "$ada" ] && [ "$(calls "" "$st")" -gt "$all" ] || continue
  score=$(score)
  say "score ${score:-none}"
  [ "$score" = 1.0 ] && break
done
[ "$score" = 1.0 ] && ! watched && say "FAIL: the watch link shows nothing" && score=""
status
groups=$([ $modal = 1 ] || for f in "$data/workers/$run"+*.json; do [ -f "$f" ] && pid "$f"; done)
stopped=$run
swarmory stop "$run" && run=""
sleep 15  # a stopped worker gets 10 s before SIGKILL
if [ $modal = 1 ]; then  # no sandbox of the run is left
  left=$(uv run -q --with modal==1.6.3 python -c "import modal, sys
app = modal.App.lookup('swarmory-workers')
print(sum(sb.poll() is None for sb in modal.Sandbox.list(app_id=app.app_id, tags={'run': sys.argv[1]})))" "$stopped")
  [ "$left" = 0 ] || { say "FAIL: $left sandboxes outlived the run"; score=""; }
else  # a member's token is dead once its run is stopped
  SWARMORY_LOGIN="$data/runs/$stopped/members/ada/home/.swarmory/login" swarmory me > /dev/null 2>&1 &&
    say "FAIL: ada's token still works after stop" && score=""
fi
for g in $groups; do  # nothing of a local run may outlive it
  ps -A -o pgid= | grep -qx " *$g" && say "FAIL: process group $g outlived the run" && score=""
done
[ "$score" = 1.0 ] && say "PASS: score 1.0" && exit 0
say "FAIL: score ${score:-none} (data: $data)"
exit 1
