"""Checks a run of tests/features from its records (its export, laid out by tools/report.py's records()) and from
driver.json, which tests/e2e_features.py writes beside them: every effect its brief's commands should leave. Prints a
line per check and exits 0 only when all pass.

    python3 tests/check_features.py EXPORT/RUN/records [--local] [--subscription]

driver.json holds what only the driver knows: the grant's hash, when it restarted the server, how many trace events
the watch link served for each agent, the viewer page's status, and the budget run's outcome. --local: a local server,
whose agents reach any host. --subscription: a run on subscription logins, which cannot call another provider's model,
so no agent and no job is translated. """
import hashlib
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).with_name("features") / "tools"))
from trajectory import summary  # noqa: E402

AGENTS = ("ada", "grace", "linus", "turing")
TRANSLATED = ("ada", "grace", "linus")  # each runs on a model of another provider than its harness's
JOBS = [  # (agent, model, budget, attempts): what the job should end as
    (("oracle", None, None, 1), "passes"), (("nop", None, None, 1), "fails"),
    (("claude-code", "claude-haiku-5-5", None, 1), "passes, priced"),
    (("claude-code", "openai/gpt-6.1-sol", 0.5, 1), "passes, translated"),
    (("codex", "anthropic/claude-sonnet-5-5", 0.5, 1), "passes, translated"),
    (("claude-code", "claude-haiku-5-5", 0.0001, 1), "stops at its budget"),
    (("oracle", None, None, 3), "is canceled"),
]
HARMLESS = {"CLAUDE_CODE_MESSAGING_TOKEN",  # Claude Code makes it for its own session, one per agent
            "GPG_KEY"}  # the Python image's release signing key id, public and the same on every Modal agent


def lines(path: Path) -> list:
    """A JSON-lines file's records. A copy taken while the server writes can end in a torn line, which is
    skipped."""
    out = []
    for x in path.read_text(errors="replace").splitlines() if path.is_file() else ():
        try:
            out.append(json.loads(x))
        except ValueError:
            pass
    return out


def in_tar(path: Path, text: str) -> bool:
    if not path.is_file():
        return False
    with tarfile.open(path) as tar:
        return any(text in tar.extractfile(m).read().decode(errors="replace") for m in tar.getmembers() if m.isfile())


def main(records: Path, local: bool, subscription: bool) -> int:
    log, ledger = lines(records / "server" / "log.jsonl"), lines(records / "server" / "ledger.jsonl")
    run = json.loads((records / "run.json").read_text())
    driver = json.loads((records / "driver.json").read_text()) if (records / "driver.json").is_file() else {}
    said = [e for e in log if e["type"] == "message"]
    general = [m for m in said if m["conv"] == "general"]
    files = records / "server" / "files"
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail and not ok else ""))

    def last(author, prefix, conv="general"):
        hits = [m for m in said if m["conv"] == conv and m["author"] == author and m["text"].startswith(prefix)]
        return hits[-1]["text"] if hits else ""

    check("the run has its $20 budget", run.get("budget") == 20, run.get("budget"))
    attached = [m for m in general
                if m["author"] == "grace" and m["text"].startswith("attached file") and m.get("files")]
    fid = attached[-1]["files"][0]["id"] if attached else None
    check("grace attached a file", fid and (files / fid).is_file(), attached[-1:])
    sha = hashlib.sha256((files / fid).read_bytes()).hexdigest()[:16] if fid and (files / fid).is_file() else None

    for a in AGENTS:
        own = [r for r in ledger if r["who"] == a and "job" not in r]
        check(f"{a} made model calls", own, "no calls in the ledger")
        priced = [r for r in own if (r.get("usd") or 0) > 0]
        check(f"{a}'s calls are priced", own and len(priced) == len(own), f"{len(priced)} of {len(own)}")
        if a in TRANSLATED and not subscription:
            check(f"{a}'s calls went through LiteLLM", any(r.get("via") == "litellm" for r in own),
                  sorted({r["model"] for r in own}))
        spend = last(a, f"spend {a}:")
        check(f"{a} posted `swarmory spend`", "$" in spend and "cost" in spend, spend[:200])
        env = last(a, f"env {a} ")
        try:
            hashes = json.loads(env.split(" ", 2)[2])
        except (IndexError, ValueError):
            hashes = None
        held = {k: v for k, v in (hashes or {}).items() if k not in HARMLESS}
        check(f"{a} holds only its SWARMORY_TOKEN and the grant", held.get("SWARMORY_TOKEN") and driver.get("grant")
              in held.values() and set(held.values()) <= {held["SWARMORY_TOKEN"], driver.get("grant")}, env[:300])
        if not local:
            check(f"{a} reaches the allowed host and no other", last(a, f"egress {a} ") ==
                  f"egress {a} example.com=200 api.anthropic.com=blocked", last(a, f"egress {a} "))
        check(f"{a} pushed checks/{a}.txt", (records / "main" / "checks" / f"{a}.txt").is_file()
              and f"{a} was here" in (records / "main" / "checks" / f"{a}.txt").read_text())
        check(f"{a} said done", any(m["author"] == a and m["text"].strip() == f"done {a}" for m in general))
        check(f"{a}'s session traces reached the server", driver.get("traces", {}).get(a, 0) > 0,
              driver.get("traces", {}).get(a))
        if driver.get("restart_at"):
            p2 = [m for m in general if m["author"] == a and m["text"].startswith(f"phase2 {a} ")
                  and m["at"] > driver["restart_at"]]
            check(f"{a} read the file and main again after the server restarted",
                  p2 and p2[-1]["text"].split()[2:4] == [sha, str(len(AGENTS))], p2[-1:] and p2[-1]["text"])
    failed = [f"{m['author']}: {m['text'][:200]}" for m in said if m["text"].startswith("failed ")]
    check("no agent reported a failed step", not failed, failed)
    check("the server was restarted mid-run", driver.get("restart_at"), "the driver never restarted it")
    check("the viewer page loads", driver.get("view") == 200, driver.get("view"))

    issues = [e for e in log if e["type"] == "issue" and e.get("title") == "feature check"]
    iid = issues[0]["id"] if issues else None
    check("ada opened the issue, assigned to grace", issues and "grace" in (issues[0].get("assignees") or []))
    check("grace commented ok on it", any(m["conv"] == iid and m["author"] == "grace" and "ok" in m["text"].lower()
                                          for m in said))
    check("ada moved it to done", any(e["type"] == "move" and e.get("id") == iid and e.get("status") == "done"
                                      for e in log))
    check("ada made the channel feature", any(e["type"] == "channel" and e["id"] == "feature" and e.get("by") == "ada"
                                              for e in log))
    check("ada's @mention woke linus, who joined and said hi",
          any(m["conv"] == "feature" and m["author"] == "ada" and "linus" in m.get("mentions", []) for m in said)
          and any(e["type"] == "join" and e["id"] == "feature" and e["member"] == "linus" for e in log)
          and any(m["conv"] == "feature" and m["author"] == "linus" and "hi from linus" in m["text"] for m in said))
    found = last("ada", "found ada ").split()
    check("ada read her DM to grace back", len(found) == 3 and found[2].isdigit() and int(found[2]) >= 1, found)
    check("linus downloaded grace's file intact", sha and last("linus", "file linus ") == f"file linus {sha}",
          (last("linus", "file linus "), sha))

    asked = {e["id"]: e for e in log if e["type"] == "job"}
    ended = {e["id"]: e for e in log if e["type"] in ("finish", "cancel")}
    for (agent, model, budget, attempts), want in JOBS:
        name = f"job {agent}" + (f" {model}" if model else "") + (f" ${budget}" if budget else "") + \
               (f" x{attempts}" if attempts > 1 else "")
        ids = [j for j, e in asked.items() if e["path"] == "tasks/hello" and
               (e["params"]["agent"], e["params"]["model"], e.get("budget"), e["params"]["attempts"])
               == (agent, model, budget, attempts)]
        if subscription and want == "passes, translated":
            continue
        if not ids:
            check(name, False, "never asked for")
            continue
        jid = ids[-1]
        end = ended.get(jid) or {}
        outcome = end.get("outcome") or {}
        s, err = outcome.get("summary") or {}, outcome.get("error") or ""
        rows = [r for r in ledger if r.get("job") == jid]
        usd = sum(r.get("usd") or 0 for r in rows)
        got = f"{end.get('status', 'unfinished')}, {s.get('passed')}/{s.get('trials')}, {len(rows)} calls, ${usd:.4f}"
        if want == "is canceled":
            ok = any(e["type"] == "cancel" and e["id"] == jid for e in log)
        elif want == "passes":
            ok = end.get("status") == "done" and s.get("trials") == 1 and s.get("passed") == 1
        elif want == "fails":
            ok = end.get("status") == "done" and s.get("trials") == 1 and s.get("passed") == 0
        elif want == "stops at its budget":  # the gateway refused a trial's call: the trial's own logs say so
            ok = (end.get("status") in ("done", "failed") and rows and usd >= budget
                  and in_tar(files / (end.get("artifacts") or "-"), f"Job {jid} has spent"))
        else:
            ok = (end.get("status") == "done" and s.get("passed") == 1 and rows
                  and all(r.get("usd") is not None for r in rows) and (budget is None or usd <= budget * 1.5)
                  and (want != "passes, translated" or all(r.get("via") == "litellm" for r in rows)))
        check(f"{name} {want}", ok, got + (f"; {err[:200]}" if err else ""))
    check("turing posted the job list", last("turing", "jobs turing:"))
    trees = {e["tree"] for e in asked.values()}
    canceled = {e["id"] for e in log if e["type"] == "cancel"}
    finished = [j for j in asked if (ended.get(j) or {}).get("type") == "finish" and j not in canceled]
    found = last("turing", "results turing:")
    check("every job ran on one tree, and results lists the finished ones by it",
          len(trees) == 1 and finished and all(f"{j} " in found for j in finished), (trees, finished, found[:300]))
    shown = [j for j, e in asked.items() if (e["params"]["agent"], e["params"]["model"], e.get("budget"))
             == ("claude-code", "claude-haiku-5-5", None)]
    folder = files / ((ended.get(shown[-1]) or {}).get("artifacts") or "-") if shown else None
    want = "\n".join(summary(str(folder))) if folder and folder.is_file() else None
    got = [m["text"].strip() for m in general if m["author"] == "turing" and m["text"].startswith("trace ")]
    check("turing read the job's Harbor trace from its artifacts",
          want and "steps None" not in want and want in got, (got[-1:], want))

    small = driver.get("budget_run") or {}
    check("a small run is refused past its budget, and its agent is told",
          small.get("refused") and small.get("usd", 0) >= small.get("budget", 1), small)
    print(f"{sum(results)} of {len(results)} checks pass")
    return 0 if all(results) else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(Path(args[0]), "--local" in args, "--subscription" in args))
