"""Runs side by side, a column each, from their exports: messages, issues, wakes, pushes, commits, model calls, tokens
and cost.

    uv run tools/compare.py RUN_FOLDER...

Each RUN_FOLDER is a run's export, as `swarmory export RUN` unpacks it."""
import json
import sys
import tempfile
from pathlib import Path

from swarmory.cli import git
from swarmory.control_plane.proxy import add, money
from swarmory.control_plane.team import replay


def tally(ledger: Path) -> dict:
    """Calls, tokens and dollars by member, from a run's ledger."""
    by = {}
    replay(ledger, lambda row: add(by, row), repair=False)
    return by


def facts(run: Path) -> dict:
    """What compare shows of one run. Its minutes active run from the log's first event to its last."""
    config, events = json.loads((run / "config.json").read_text()), []
    replay(run / "log.jsonl", events.append, repair=False)
    said = [e for e in events if e["type"] == "message" and not e["activity"]]
    board = list({e["id"]: e.get("status", "todo") for e in events if e["type"] in ("issue", "move")}.values())
    pushed = [e["ok"] for e in events if e["type"] == "push"]
    with tempfile.TemporaryDirectory() as tmp:
        main = (run / "work.bundle").is_file() and git(run, "clone", "-q", "--bare", "work.bundle", tmp) is not None \
            and git(tmp, "for-each-ref", "refs/heads/main")
        commits = int(git(tmp, "rev-list", "--count", "refs/heads/main")) if main else 0
    by = tally(run / "ledger.jsonl").values()
    kinds = [f"{m['harness']}:{m.get('model') or 'default'}" for m in config["members"] if m["kind"] == "agent"]
    return {"run": config["run"], "mission": config["mission"],
            "team": "\n".join(f"{kinds.count(k)} {k}" for k in dict.fromkeys(kinds)),
            "minutes active": round((events[-1]["at"] - events[0]["at"]) / 60) if events else 0,
            "messages": len(said), "of them DMs": sum(e["conv"].startswith("dm:") for e in said),
            "issues opened": len(board), "issues done": board.count("done"),
            "issues doing at the end": board.count("doing"), "wakes": sum(e["type"] == "woke" for e in events),
            "pushes ok / refused": f"{sum(pushed)} / {len(pushed) - sum(pushed)}" if pushed else "not logged",
            "commits on main": commits, "model calls": sum(u["calls"] for u in by),
            "tokens in / out": f"{sum(u['input'] + u['cached'] + u['written'] for u in by):,} / "
                               f"{sum(u['output'] for u in by):,}",
            "cost at list prices": money(sum(u.get("usd") or 0 for u in by))}


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cols = [facts(Path(r)) for r in sys.argv[1:]]
    table = [[k, *(str(c[k]) for c in cols)] for k in cols[0]]
    widths = [max(len(line) for row in table for line in row[j].split("\n")) for j in range(len(table[0]))]
    for row in table:
        cells = [cell.split("\n") for cell in row]
        for i in range(max(map(len, cells))):
            print("  ".join(f"{c[i] if i < len(c) else '':{w}}" for c, w in zip(cells, widths)).rstrip())


if __name__ == "__main__":
    main()
