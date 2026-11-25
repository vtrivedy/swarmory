#!/bin/bash
# The Harbor jobs end-to-end test: a run of tests/mission with one agent, in which the test, as the operator, asks for
# Harbor jobs on tests/eval-task/hello, which writes "hello" to a file: oracle (passes), nop (fails) and claude-code
# with claude-haiku-5-5 (passes, through a model). A fourth job runs the custom harness tests/eval-task/harness on
# tests/eval-task/mcp-word, whose answer only the task's own MCP server tells (passes, through a model). It runs only
# on API keys: a Claude subscription login answers Claude Code alone.
# `swarmory job` sends this checkout's HEAD, so commit before you run it. A job worker runs each with the real Harbor:
# locally on your machine, its trials in Docker (which must be running); on Modal in a worker sandbox, its trials in
# Modal sandboxes. The model trials call the server's model gateway with the job's spend token, which the test checks
# the server counted. It passes once each job's result message has come with the right score, and fails after 25
# minutes.
#
#   tests/e2e_evals.sh            its own local server, on a fresh data folder and a free port
#   tests/e2e_evals.sh --modal    deploys the server, then a run on the Modal server `swarmory login` points at; needs
#                                 the Modal secret swarmory-modal with MODAL_TOKEN_ID and MODAL_TOKEN_SECRET
#
# Model calls use the server's ANTHROPIC_API_KEY (locally, your shell's), or its
# CLAUDE_CODE_OAUTH_TOKEN login when E2E_SUBSCRIPTION is.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"  # this checkout's swarmory, for the server, swarmory and tools/
modal=0
[ "${1:-}" = --modal ] && modal=1
extra=""
[ $modal = 1 ] && extra="--extra modal"  # deploy needs the Modal client
PY=$(uv run -q $extra python -c 'import sys; print(sys.executable)') || exit 1  # this checkout's swarmory
swarmory() { "$PY" -m swarmory.cli "$@"; }
say() { echo "e2e-evals $(date +%H:%M:%S) $*"; }
args=(start tests/mission --hours 0.5 --budget 5 --team claude-code:claude-haiku-5-5=1 --jobs)
[ -n "${E2E_SUBSCRIPTION:-}" ] && args+=(--subscription)
data=$(mktemp -d "${TMPDIR:-/tmp}/e2e-evals.XXXXXX")
run="" server=""
fail() {
  say "FAIL: $* (data: $data)"
  [ -n "$run" ] && swarmory stop "$run" > /dev/null 2>&1
  [ -n "$server" ] && kill "$server" 2> /dev/null
  exit 1
}

url() {  # the server's address, from the login file
  "$PY" -c "import json, os
print(json.load(open(os.environ.get('SWARMORY_LOGIN') or os.path.expanduser('~/.swarmory/login')))['url'])"
}
up() {  # waits up to 5 minutes for the server to answer /health (a first start installs LiteLLM)
  local i
  for i in $(seq 150); do curl -sf "$(url)/health" > /dev/null && return 0; sleep 2; done
  return 1
}
if [ $modal = 1 ]; then
  swarmory deploy > "$data/deploy.log" 2>&1 || { cat "$data/deploy.log"; fail "deploy failed"; }
  case "$(url 2> /dev/null)" in
    https://*) ;;
    *) fail "log in to the Modal server first: swarmory login URL TOKEN" ;;
  esac
  up || fail "the deployed server does not answer"  # a deploy starts a new container
else
  export SWARMORY_LOGIN="$data/login"  # the person's own login stays as it is
  port=$("$PY" -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])")
  token=$("$PY" -c "import secrets; print(secrets.token_hex(16))")
  SWARMORY_OPERATOR_TOKEN="$token" "$PY" -m swarmory.cli serve --data "$data" --port "$port" > "$data/server.log" 2>&1 &
  server=$!
  swarmory login "http://127.0.0.1:$port" "$token" > /dev/null
  up || { cat "$data/server.log"; fail "the server did not start"; }
fi

say "swarmory ${args[*]}"
swarmory "${args[@]}" > "$data/start.log" 2>&1 || { cat "$data/start.log"; fail "start failed"; }
run=$(awk '/^run /{sub(":", "", $2); print $2; exit}' "$data/start.log")
[ -n "$run" ] || { cat "$data/start.log"; fail "no run in start's output"; }
say "run $run"
swarmory use "$run" > /dev/null || fail "swarmory use $run"

for agent in oracle nop; do
  swarmory job harbor tests/eval-task/hello --agent $agent --to general || fail "swarmory job harbor --agent $agent"
done
swarmory job harbor tests/eval-task/hello --agent claude-code --model claude-haiku-5-5 --to general ||
  fail "swarmory job harbor --agent claude-code"
jobs=3 billed=j3
if [ -z "${E2E_SUBSCRIPTION:-}" ]; then  # a Claude login answers Claude Code alone, not a harness of our own
  swarmory job harbor tests/eval-task/mcp-word --agent tests/eval-task/harness --model claude-haiku-5-5 --to general ||
    fail "swarmory job harbor --agent tests/eval-task/harness"
  jobs=4 billed="j3 j4"
fi
end=$(($(date +%s) + 1500))
until [ "$(swarmory job list | grep -cE '^j[0-9]+ +(done|failed|canceled) ')" = $jobs ]; do
  [ "$(date +%s)" -lt "$end" ] || { swarmory job list; fail "the jobs did not all end in 25 minutes"; }
  sleep 20
done
swarmory job list
expect() {  # expect JOB "RESULT": the job's line in `swarmory job list` ends with RESULT
  swarmory job list | grep -q "^$1 done .*$2\$" || { swarmory job show "$1"; fail "$1 is not '$2'"; }
}
expect j1 "1 passed, 0 failed, 0 errored of 1"
expect j2 "0 passed, 1 failed, 0 errored of 1"
expect j3 "1 passed, 0 failed, 0 errored of 1"
[ $jobs = 4 ] && expect j4 "1 passed, 0 failed, 0 errored of 1"
said=$(swarmory read --all --limit 1000)
for j in $(seq -f 'j%g' 1 $jobs); do
  grep -qE "swarmory: @[^ ]+ job $j finished" <<< "$said" || fail "no result message for $j"
done
found=$(swarmory results tests/eval-task/hello | grep -c '^j[0-9]* *done ')
[ "$found" = 3 ] || fail "results lists $found of the 3 jobs on tests/eval-task/hello"
# each model trial called the server's model gateway with its job's spend token, which the server billed to the job
for j in $billed; do
  swarmory job show $j | grep -qE "^model calls: [1-9]" ||
    { swarmory job show $j; fail "$j's trial made no model call through the gateway"; }
  swarmory job show $j
done
swarmory stop "$run" > /dev/null 2>&1
run=""
[ -n "$server" ] && kill "$server" 2> /dev/null
say "PASS"
