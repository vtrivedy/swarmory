import os
"""The audit's scenarios A to D (audit-kernel/demo.py), on the prototype with squads.py loaded as a piece."""
import json, sys, tempfile, time, types
from pathlib import Path
sys.path.insert(0, os.environ.get("SWARMORY_REPO", "."))
from swarmory.control_plane.team import Team
from swarmory.control_plane.policy import sha

SQUAD = {"ada": "red", "grace": "red", "alan": "blue"}
SRC = Path(__file__).with_name("squads.py").read_text()

def make(pieces=True):
    d = Path(tempfile.mkdtemp())
    ms = [{"id": x, "name": x.title(), "kind": "agent", "tags": ["squad:" + g], "token_sha256": sha(x)} for x, g in SQUAD.items()]
    ms.append({"id": "vivek", "name": "Vivek", "kind": "human", "operator": True})
    (d / "config.json").write_text(json.dumps({"members": ms, "channels": [{"id": "general", "about": ""}], "created": 0,
                                               **({"pieces": [{"name": "squads", "source": SRC}]} if pieces else {})}))
    t = Team(d)
    return t, types.SimpleNamespace(team=t, config={"created": 0})

ids = lambda r: [m["id"] for m in r["messages"]]
sync = lambda run: run.team.pieces.every("tick", run, 7 * 3600)

print("ownership table:"); t, run = make(); print("  " + "\n  ".join(t.pieces.table()))

print("A. blue @mentions red in general while closed; a red message follows; then the sync")
t.send("alan", {"to": "general", "text": "@ada the API contract changed"})   # m1: held for ada
t.send("grace", {"to": "general", "text": "@ada status?"})                    # m2: same squad
print("  m1 event:", {k: v for k, v in json.loads((t.dir / "log.jsonl").read_text().splitlines()[-2]).items() if k in ("recipients", "audience", "held", "route")})
print("  ada inbox before sync:", ids(t.inbox("ada", {})), "| read general:", ids(t.read("ada", {"conv": "general"})),
      "| read --all:", ids(t.read("ada", {"conv": "general", "all": True})), "| mark:", t.marks.get(("ada", "general")))
sync(run)
got = t.inbox("ada", {})["messages"]
print("  after sync: inbox ->", [(m["id"], m["text"].splitlines()[1]) for m in got], "| again ->", ids(t.inbox("ada", {})),
      "| read general ->", ids(t.read("ada", {"conv": "general"})))
print("  grace (same squad as ada, nothing held) reads --all:", ids(t.read("grace", {"conv": "general", "all": True})))
print("  alan reads --all:", ids(t.read("alan", {"conv": "general", "all": True})), "| operator:", ids(t.read("vivek", {"conv": "general", "all": True})))

print("B. a held DM is not readable before the sync, by any path")
t, run = make()
t.send("alan", {"to": "ada", "text": "held DM"})
print("  inbox:", ids(t.inbox("ada", {})), "| read alan --all:", ids(t.read("ada", {"conv": "alan", "all": True})), "| me.unread:", t.me("ada", {})["unread"])

print("D. held m1, then unaddressed m2 and addressed m3 from her own squad; then the sync")
t, run = make()
t.send("alan", {"to": "ada", "text": "held DM"})
t.send("grace", {"to": "general", "text": "fyi, no mention"})
t.send("grace", {"to": "general", "text": "@ada ping"})
sync(run)
a, b = ids(t.inbox("ada", {})), ids(t.inbox("ada", {}))
print("  1st inbox:", a, "| 2nd inbox:", b, "| read (rest unread):", ids(t.read("ada", {})))

print("E. replay gives the same inboxes, marks and piece state")
t2 = Team(t.dir)
same = (t2.inboxes == t.inboxes, t2.marks == t.marks, t2.state == t.state)
print("  ", same)

print("F. the sync is open: a cross-squad message is delivered as today; operator and server are never routed by the piece")
t.send("alan", {"to": "ada", "text": "now open"}); print("  inbox:", [m["text"] for m in t.inbox("ada", {})["messages"]])
t, run = make(); t.announce("@all budget set"); print("  @all from the server reaches:", t.recipients["m1"])

print("G. two listed pieces that both own route are refused at load")
from swarmory.control_plane.pieces import Pieces
try: Pieces([{"name": "a", "source": "def route(t, d): pass"}, {"name": "b", "source": "def route(t, d): pass"}])
except ValueError as e: print("  ", e)
