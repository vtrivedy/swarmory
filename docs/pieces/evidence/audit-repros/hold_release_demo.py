import os
"""Emulates the proposal's Hold/release + visible semantics literally on the real Team (commit 07932e6):
 deliver seam -> held recipients are left out of the logged `recipients`; a later `release` event fills inboxes;
 visible seam -> wraps Team.sees()."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ.get("SWARMORY_REPO", "."))
from swarmory.control_plane.team import Team
from swarmory.control_plane.policy import sha

SQUAD = {"ada": "red", "grace": "red", "alan": "blue"}

class T(Team):
    def __init__(self, d, visible):
        self.open, self.use_visible, self.held = False, visible, []
        super().__init__(d, also=self.release)
    def release(self, ev):                       # "a later release event fills inboxes"
        if ev["type"] == "release":
            self.inboxes.setdefault(ev["member"], []).append(self.messages[ev["id"]])
    def addressed(self, conv, author, mentions, reply_to):   # stream kind's addressed(), then the deliver seam
        out = []
        for x in super().addressed(conv, author, mentions, reply_to):
            if SQUAD[x] == SQUAD[author] or self.open: out.append(x)
            else: self.held.append((x, f"m{self.mcount + 1}"))
        return out
    def sees(self, x, m):                        # visible seam after the built-in
        if self.use_visible and not self.open and m["conv"] == "general" and SQUAD[m["author"]] != SQUAD[x]:
            return False
        return super().sees(x, m)
    def open_window(self):
        with self.lock:
            self.open = True
            for x, mid in self.held:
                self.append({"type": "release", "member": x, "id": mid, "recipients": [x]})

def make(visible):
    d = Path(tempfile.mkdtemp())
    ms = [{"id": x, "name": x.title(), "kind": "agent", "token_sha256": sha(x)} for x in SQUAD]
    (d / "config.json").write_text(json.dumps({"members": ms, "channels": [{"id": "general", "about": ""}]}))
    return T(d, visible)

ids = lambda r: [m["id"] for m in r["messages"]]

print("A. deliver+visible (squads as specified): blue @mentions red in general while closed")
t = make(True)
t.send("alan", {"to": "general", "text": "@ada the API contract changed"})      # m1 held for ada
t.send("grace", {"to": "general", "text": "@ada status?"})                       # m2 same squad
print("  inbox before window:", ids(t.inbox("ada", {})), " mark:", t.marks.get(("ada", "general")))
t.open_window()
print("  after release: inboxes has", [m["id"] for m in t.inboxes["ada"]],
      "| undelivered:", [m["id"] for m in t.undelivered("ada")],
      "| inbox():", ids(t.inbox("ada", {})), "| read general:", ids(t.read("ada", {"conv": "general"})))

print("B. deliver only (no visible handler): held mail is readable at once")
t = make(False)
t.send("alan", {"to": "general", "text": "@ada secret blue plan"})
t.send("alan", {"to": "ada", "text": "held DM"})
print("  inbox:", ids(t.inbox("ada", {})), "| read general:", ids(t.read("ada", {"conv": "general"})),
      "| read alan --all:", ids(t.read("ada", {"conv": "alan", "all": True})), "| me.unread:", t.me("ada", {})["unread"])

print("C. visible only hooks sees(): --all, issue and files go through hidden(), not sees()")
t = make(True)
t.send("alan", {"to": "general", "text": "blue-only note"})
print("  read general:", ids(t.read("ada", {"conv": "general"})), "| read general --all:", ids(t.read("ada", {"conv": "general", "all": True})))

print("D. release appends an older seq behind a newer one: a message is delivered twice")
t = make(False)
t.send("alan", {"to": "ada", "text": "held DM"})                 # m1 held
t.send("grace", {"to": "general", "text": "fyi, no mention"})   # m2 unread in general, not addressed
t.send("grace", {"to": "general", "text": "@ada ping"})         # m3 addressed
t.open_window()
print("  1st inbox:", ids(t.inbox("ada", {})), "| 2nd inbox:", ids(t.inbox("ada", {})))
