"""Experiments against the real Team (read-only import). Hold/Release is emulated exactly as the proposal words it:
the message event logs `recipients` without the held member plus a `held` map; a later `release` event fills inboxes."""
import json, sys, tempfile, time
from pathlib import Path
from swarmory.control_plane import team as T

def mk(members, channels=("general",)):
    d = Path(tempfile.mkdtemp())
    cfg = {"members": [{"id": m, "name": m.title(), "kind": "agent"} for m in members],
           "channels": [{"id": c, "about": ""} for c in channels]}
    (d / "config.json").write_text(json.dumps(cfg))
    return d

class Held(T.Team):
    """Minimal proposal semantics. hold(author, to) -> bool is the `deliver` seam; visible(x, m) the `visible` seam."""
    hold_fn = staticmethod(lambda a, x: False)
    visible_fn = staticmethod(lambda x, m: True)
    def __init__(self, d):
        self.heldmap = {}
        super().__init__(d)
    def addressed(self, conv, author, mentions, reply_to):
        full = super().addressed(conv, author, mentions, reply_to)
        self._held = [x for x in full if self.hold_fn(author, x)]
        return [x for x in full if x not in self._held]
    def apply(self, ev):
        if ev["type"] == "release":
            self.seq = ev["seq"]
            for mid, x in ev["pairs"]:
                self.inboxes.setdefault(x, []).append(self.messages[mid])   # "a later release event fills inboxes"
            return
        super().apply(ev)
    def post(self, *a, **k):
        m = super().post(*a, **k)
        for x in self._held:
            self.heldmap.setdefault(x, []).append(m["id"])
        return m
    def release(self):
        with self.lock:
            pairs = [(mid, x) for x, ids in self.heldmap.items() for mid in ids]
            self.heldmap = {}
            self.append({"type": "release", "pairs": pairs, "recipients": [x for _, x in pairs]})
    def sees(self, x, m):
        return super().sees(x, m) and self.visible_fn(x, m)

squad = {"cc1": "red", "cc2": "red", "cx1": "red", "cc3": "blue", "cx2": "blue"}
cross = lambda a, x: squad.get(a) != squad.get(x)

def ids(r): return [m["id"] for m in r["messages"]]

print("== E1a: held issue comment, visible hides it; later same-squad comment read; then release")
t = Held(mk(squad)); t.hold_fn = cross
t.visible_fn = lambda x, m: not (m["id"] in t.heldmap.get(x, ()))   # hidden while held
t.new("cc3", {"title": "shared", "assignees": ["cc3", "cc1"]})
h = t.send("cc1", {"to": "i1", "text": "red result: API contract is final"})["message"]
t.send("cx2", {"to": "i1", "text": "@cc3 blue note"})
print(" held for cc3:", t.heldmap.get("cc3"), "| inbox delivery to cc3:", ids(t.inbox("cc3", {})))
print(" mark(cc3,i1) =", t.marks.get(("cc3", "i1")), " held msg seq =", h["seq"])
t.release()
print(" after release: inbox ->", ids(t.inbox("cc3", {})), "| read ->", ids(t.read("cc3", {})),
      "| me.unread ->", t.me("cc3", {})["unread"])
print(" => held message", h["id"], "never shown to cc3 by inbox, next or read: LOST")

print("== E1b: same, but no visible hook (squads only hides `general`): early read paths")
t = Held(mk(squad)); t.hold_fn = cross
t.new("cc3", {"title": "shared", "assignees": ["cc3", "cc1"]})
h = t.send("cc1", {"to": "i1", "text": "SECRET red result"})["message"]
print(" inbox(cc3) ->", ids(t.inbox("cc3", {})), "(held, so nothing)")
print(" me(cc3).unread ->", t.me("cc3", {})["unread"])
print(" issue(i1) thread ->", [m["text"] for m in t.issue("cc3", {"id": "i1"})["messages"] if not m["activity"]])
print(" read --all ->", [m["text"] for m in t.read("cc3", {"all": True})["messages"] if not m["activity"]])
print(" read i1 ->", [m["text"] for m in t.read("cc3", {"conv": "i1"})["messages"] if not m["activity"]])

print("== E1c: held DM is readable by its recipient before release")
t = Held(mk(squad)); t.hold_fn = cross
t.send("cc1", {"to": "cc3", "text": "SECRET dm"})
print(" inbox(cc3) ->", ids(t.inbox("cc3", {})), "| read --all dm ->",
      [m["text"] for m in t.read("cc3", {"conv": "cc1", "all": True})["messages"]])

print("== E2: release appended behind newer pending mail; one delivery of 8 drops the released ones")
t = Held(mk(squad)); t.hold_fn = cross
h = t.send("cc1", {"to": "cc3", "text": "held dm"})["message"]
for i in range(8):
    t.send("cx2", {"to": "cc3", "text": f"blue {i}"})
t.release()
print(" inbox order (seq):", [m["seq"] for m in t.inboxes["cc3"]])
got = ids(t.inbox("cc3", {}))
print(" delivery 1 ->", got, "| delivery 2 ->", ids(t.inbox("cc3", {})), "| inbox left:", [m["id"] for m in t.inboxes["cc3"]])
print(" held", h["id"], "shown by a delivery?", h["id"] in got, "(it is only left as unread:", t.me("cc3", {})["unread"], ")")

print("== E3: today's read paths ignore sees(): a non-member reads a private channel")
t = T.Team(mk(squad))
t.channel("cc1", {"name": "red"})
t.send("cc1", {"to": "red", "text": "red-only plan"})
print(" cc3 in #red?", "cc3" in t.channels["red"]["members"])
print(" read --all (cc3) ->", [m["text"] for m in t.read("cc3", {"all": True})["messages"]])
print(" read red --all (cc3) ->", [m["text"] for m in t.read("cc3", {"conv": "red", "all": True})["messages"]])
with t.lock: t.append({"type": "file", "id": "f1", "name": "x", "size": 1, "by": "cc1"})
t.send("cc1", {"to": "red", "files": ["f1"]})
print(" may_get(cc3, f1 shared only in #red) ->", t.may_get("cc3", "f1"))
print(" join red (cc3) ->", t.join("cc3", {"name": "red"})["channel"]["members"])

print("== E4: hub_routing rewrites DMs only; a channel @mention reaches the peer directly")
t = T.Team(mk(squad))
m = t.send("cc2", {"to": "general", "text": "@cx1 is the API contract final?"})["message"]
print(" recipients ->", t.recipients[m["id"]])

print("== E5: the proposal's example plugin on a server-authored message")
t = T.Team(mk(squad)); t.announce("@all The run ends in about 10 minutes.")
m = t.order[-1]
try:
    t.members[m["author"]].get("group")
except KeyError as e:
    print(" Squads.group(author) -> KeyError", e, "| author is SYSTEM =", T.SYSTEM)

print("== E6: Team.lock is not reentrant: a verb called from inside a seam under the lock")
t = T.Team(mk(squad))
with t.lock:
    print(" second acquire ->", t.lock.acquire(timeout=0.2))
