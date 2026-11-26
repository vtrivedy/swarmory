"""The team: members, channels, DMs, issues, messages and pushes, kept as events in one append-only log
(DATA/runs/RUN/log.jsonl) and replayed into memory when the run loads. One lock covers every write, so the file's order
is the order everyone sees.

No HTTP here. verbs.py calls one method per verb, each taking the caller's member id and the request dict and returning
the response dict. A ValueError means a bad request. NotFound means an unknown conversation, channel or issue."""
import hmac
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

from swarmory import HOLD

from .policy import sha

MENTION = re.compile(r"(?<![\w@])@([A-Za-z][\w-]*)")
CHANNEL = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
STATUSES = ("todo", "doing", "done", "canceled")
TURN_MOST = 10  # messages a turn shows; `swarmory read` has the rest
INBOX_MOST = 8  # messages one delivery between tool calls carries
SETTLE = 10  # seconds a held agent waits for the rest of a burst, so a teammate's run of messages is one turn
IDLE = 240  # seconds a waiting agent's next turn waits for a message before it starts anyway
SYSTEM = "swarmory"  # the author of what the server posts itself, such as a job's result: no member's id


class NotFound(Exception):
    pass


def string(req: dict, key: str, most: int, need: bool = True):
    value = req.get(key)
    if value is None and not need:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > most:
        raise ValueError(f"{key} must be 1 to {most} characters of text")
    return value


def number(req: dict, key: str, default: float, low: float, high: float) -> float:
    value = req.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not abs(value) <= sys.float_info.max:
        raise ValueError(f"{key} must be a number")
    return min(max(value, low), high)


def replay(path: Path, apply, repair: bool = True):
    """apply() each line of a JSON-lines log in order, and return the last. A torn last line (a write a crash cut
    short) is skipped, and with `repair` cut off, so the next write starts a line of its own; a bad line before the
    last is an error."""
    data = path.read_bytes() if path.exists() else b""
    lines, good, ev = data.splitlines(keepends=True), 0, None
    for i, line in enumerate(lines):
        try:
            ev = json.loads(line)
        except ValueError:
            if i < len(lines) - 1:
                raise ValueError(f"{path} line {i + 1} is not JSON; fix or remove it by hand")
            break
        apply(ev)
        good += len(line)
    if repair and (good < len(data) or not data.endswith(b"\n")):
        with open(path, "ab") as f:
            f.truncate(good)
            if good and not data[:good].endswith(b"\n"):
                f.write(b"\n")
    return ev


def activity(ev: dict) -> str:
    """The line a board change posts in its issue."""
    if ev["type"] == "issue":
        return f"opened {ev['id']}: {ev['title']}"
    if ev["type"] == "assign":
        return ", ".join(w + ", ".join(ev[k]) for w, k in (("assigned ", "add"), ("unassigned ", "remove")) if ev[k])
    return f"moved to {ev['status']}" + (f": {ev['note']}" if ev.get("note") else "")


class Team:
    def __init__(self, data, also=None):
        """`also` applies the events this file does not know, live and on replay: jobs.py's."""
        self.dir, self.also = Path(data), also
        config = json.loads((self.dir / "config.json").read_text())
        self.lock = threading.Lock()
        self.bells = defaultdict(lambda: threading.Condition(self.lock))  # member -> what its wait() holds on
        self.seq = self.mcount = self.icount = self.fcount = 0
        self.members, self.channels, self.board, self.messages = {}, {}, {}, {}
        self.files = {}   # file id -> {id, name, size, by}: what was uploaded, its bytes in files/<id>
        self.shared = {}  # file id -> the conversations it was sent in
        self.order = []   # every message in seq order
        self.convs = {}   # conv -> [message]
        self.recipients = {}  # message id -> the members it is for, as logged with the message
        self.inboxes = {}  # member -> the messages for them not yet delivered, oldest first
        self.marks = {}   # (member, conv) -> read upto
        self.nonces = {}  # (member, nonce) -> the event it made
        self.holds = {}   # member -> its newest held call; an older one still held (a killed agent's) must not answer
        self.waiting = {}  # member -> when it said it is waiting (`wait`), until its next turn starts
        self.started = {}  # member -> when the server last started its worker, which minted the token it holds now
        self.starts = Counter()  # member -> how many times the server has started its worker
        self.digests = set()  # every token digest the run has had, which a trace must hide
        self.turns = {}   # member -> main's commit at its last wait's answer: where the next answer's commits start
        self.path = self.dir / "log.jsonl"
        replay(self.path, self.apply)
        self.log = open(self.path, "a")
        with self.lock:  # config's members and channels go in on the first start, or finish one a crash cut short
            for m in (m for m in config["members"] if m["id"] not in self.members):
                self.append({"type": "member", "member": m})
            for c in (c for c in config["channels"] if c["id"] not in self.channels):
                self.append({"type": "channel", "id": c["id"], "about": c["about"], "by": None})

    # ---- the log

    def append(self, ev: dict) -> dict:
        ev = {"seq": self.seq + 1, "at": time.time(), **ev}
        try:
            self.log.write(json.dumps(ev) + "\n")
            self.log.flush()
            if ev["type"] not in ("read", "delivered", "turn", "wait"):  # a crash that loses one only replays a turn or wait
                os.fsync(self.log.fileno())
        except OSError as e:  # the line may be on disk but not in memory: exit, and the restart replays what landed
            os.write(2, f"swarmory server: the log write failed ({e!r}); exiting\n".encode())
            os._exit(1)
        self.apply(ev)
        for x in ev.get("recipients", ()):
            self.bells[x].notify_all()
        return ev

    def apply(self, ev: dict):
        """The one state change for an event, used by live writes and replay alike."""
        self.seq, t = ev["seq"], ev["type"]
        if t == "member":
            self.members[ev["member"]["id"]] = ev["member"]
            self.digests.update([ev["member"]["token_sha256"]] if ev["member"].get("token_sha256") else [])
        elif t == "removed":  # the operator took the member off the team: its token opens nothing now
            self.members[ev["member"]]["removed"] = ev["at"]
            self.members[ev["member"]].pop("token_sha256", None)
        elif t == "worker":  # a new worker for a member, with the only token that opens the member now
            self.members[ev["member"]]["token_sha256"] = ev["token_sha256"]
            self.digests.add(ev["token_sha256"])
            self.started[ev["member"]] = ev["at"]
            self.starts[ev["member"]] += 1
        elif t == "channel":
            self.channels[ev["id"]] = {"id": ev["id"], "about": ev["about"],
                                       "members": [ev["by"]] if ev["by"] else list(self.members)}
        elif t in ("join", "leave"):  # a channel's members or an issue's subscribers; join() logs only real changes
            issue = self.board.get(ev["id"])
            group = issue["subscribers"] if issue else self.channels[ev["id"]]["members"]
            if t == "join":
                group.append(ev["member"])
            else:
                group.remove(ev["member"])
        elif t == "message":
            self.apply_message(ev)
        elif t == "file":
            self.files[ev["id"]] = {k: ev[k] for k in ("id", "name", "size", "by")}
            self.fcount = max(self.fcount, int(ev["id"][1:]))
        elif t == "issue":
            self.icount = max(self.icount, int(ev["id"][1:]))
            self.board[ev["id"]] = {"id": ev["id"], "title": ev["title"], "body": ev["body"], "status": "todo",
                                     "assignees": list(ev["assignees"]), "author": ev["author"], "subscribers": [],
                                     "at": ev["at"], "updated": ev["at"]}
            self.subscribe(ev["id"], [ev["author"]] + ev["assignees"] + self.mentions(ev["body"]))
        elif t == "assign":
            issue = self.board[ev["id"]]
            issue["assignees"] = [a for a in issue["assignees"] if a not in ev["remove"]] + \
                                 [a for a in ev["add"] if a not in issue["assignees"]]
            issue["subscribers"] = [x for x in issue["subscribers"] if x not in ev["remove"]]
            self.subscribe(ev["id"], ev["add"])
            issue["updated"] = ev["at"]
        elif t == "move":
            self.board[ev["id"]].update(status=ev["status"], updated=ev["at"])
        elif t == "read":
            key = (ev["member"], ev["conv"])
            self.marks[key] = max(self.marks.get(key, 0), ev["upto"])
        elif t in ("delivered", "woke"):
            self.inboxes[ev["member"]] = [m for m in self.inboxes.get(ev["member"], ()) if m["seq"] > ev["upto"]]
        elif t == "wait":
            self.waiting[ev["member"]] = ev["at"]
        elif t == "turn":
            self.waiting.pop(ev["member"], None)
            x = ev["member"]
            self.turns[x] = ev.get("main")
            self.inboxes[x] = [m for m in self.inboxes.get(x, ()) if m["seq"] > ev["delivered"]]
        elif self.also:  # a job's event, or a push, which changes no state: it is logged for the viewer and an export
            self.also(ev)
        if ev.get("nonce"):
            self.nonces[(ev.get("author") or ev["by"], ev["nonce"])] = ev

    def apply_message(self, ev: dict):
        m = {k: ev[k] for k in ("id", "seq", "conv", "author", "text", "reply_to", "mentions", "activity", "at")}
        m["files"] = ev.get("files", [])
        for f in m["files"]:
            self.shared.setdefault(f["id"], set()).add(m["conv"])
        self.mcount = max(self.mcount, int(m["id"][1:]))
        self.messages[m["id"]] = m
        self.order.append(m)
        self.convs.setdefault(m["conv"], []).append(m)
        recipients = ev.get("recipients") if "recipients" in ev else ev.get("wakes", [])
        self.recipients[m["id"]] = recipients
        for x in recipients:
            self.inboxes.setdefault(x, []).append(m)
        issue = self.board.get(m["conv"])
        if issue:
            issue["updated"] = m["at"]
            if not m["activity"]:  # an activity line's mentions only tell (an unassigned member, or a body's @all)
                self.subscribe(issue["id"], [m["author"]] + m["mentions"])

    def subscribe(self, issue_id: str, ids: list):
        subs = self.board[issue_id]["subscribers"]
        subs.extend(x for x in dict.fromkeys(ids) if x in self.members and x not in subs)

    # ---- helpers, all called under the lock

    def member(self, name: str):
        name = name.lower()
        return next((m["id"] for m in self.members.values() if name in (m["id"], m["name"].lower())), None)

    def mentions(self, text: str) -> list:
        group = {"all": "all", "channel": "channel", "here": "channel"}
        found = [group.get(w.lower()) or self.member(w) for w in MENTION.findall(text)]
        return [x for x in dict.fromkeys(found) if x]

    def names(self, who: str, names) -> list:
        """Member ids for a list of names; `me` is the caller."""
        if not isinstance(names, list):
            raise ValueError("give members as a list of names")
        out = []
        for n in names:
            n = str(n).strip().lstrip("@")
            x = who if n.lower() == "me" else self.member(n)
            if not x:
                raise ValueError(f"{n} is not a member; see `swarmory who`")
            if self.members[x].get("removed"):
                raise ValueError(f"{n} has left the team; see `swarmory who`")
            if x not in out:
                out.append(x)
        return out

    def resolve(self, who: str, name, write: bool = False) -> str:
        """A conversation id for a name a person typed: general, #general, ada, @ada, Ada, i7 or dm:ada:grace."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name a channel, a member or an issue")
        name = name.strip()
        if re.fullmatch(r"i\d+", name):
            if name in self.board:
                return name
            raise NotFound(f"there is no issue {name}; see `swarmory issues`")
        if name.startswith("dm:"):
            pair = name.split(":")[1:]
            if len(pair) != 2 or pair[0] == pair[1] or not all(p in self.members for p in pair):
                raise NotFound(f"{name} is not a DM between two members")
            if who not in pair and write or self.hidden(who, name):
                raise NotFound(f"{name} is not your DM")
            return "dm:" + ":".join(sorted(pair))
        bare = name.lstrip("#@")
        if not name.startswith("@") and bare in self.channels:
            return bare
        x = None if name.startswith("#") else self.member(bare)
        if x == who:
            raise ValueError("that's you: DM someone else")
        if x:
            return "dm:" + ":".join(sorted((who, x)))
        raise NotFound(f"no channel, member or issue is called {name}; see `swarmory who`")

    def hidden(self, who: str, conv: str) -> bool:
        """conv is a DM between two others, which only the operator may read."""
        return conv.startswith("dm:") and who not in conv.split(":")[1:] and not self.members[who].get("operator")

    def seen(self, who: str, nonce, type_: str):
        """The id of what a repeated write of an event of this type made the first time, or None."""
        ev = self.nonces.get((who, nonce)) if nonce else None
        return ev["id"] if ev and ev["type"] == type_ else None

    def addressed(self, conv: str, author: str, mentions: list, reply_to) -> list:
        """Whom a new message is for."""
        if conv.startswith("dm:"):  # a mention in a DM doesn't show it to a third member
            return [x for x in conv.split(":")[1:] if x != author]
        issue = self.board.get(conv)
        here = self.channels[conv]["members"] if "channel" in mentions and conv in self.channels else ()
        replied = self.messages[reply_to]["author"] if reply_to else None
        return [x for x, m in self.members.items() if x != author and not m.get("removed") and (
            x in mentions or "all" in mentions or x in here or x == replied or issue and x in issue["subscribers"])]

    def post(self, conv: str, author: str, text: str, reply_to, nonce, change: dict = None, files=(),
             quiet: bool = False) -> dict:
        """A message, or with `change` the activity line of that board change. The line also tells whom the change
        unassigned and whom a new issue's body @mentions. A quiet message is an activity line for no one."""
        told = (change.get("body") or " ".join("@" + x for x in change.get("remove", ()))) if change else ""
        mentions = [] if quiet else self.mentions(text + "\n" + told)
        ev = self.append({"type": "message", "id": f"m{self.mcount + 1}", "conv": conv, "author": author,
                          "text": text, "reply_to": reply_to, "mentions": mentions, "activity": bool(change) or quiet,
                          "nonce": nonce,
                          "recipients": [] if quiet else self.addressed(conv, author, mentions, reply_to),
                          **({"files": [self.files[f] for f in files]} if files else {})})
        return self.messages[ev["id"]]

    def change(self, ev: dict) -> dict:
        """A board change, then its activity line in the issue, under the same lock."""
        ev = self.append(ev)
        self.post(ev["id"], ev.get("author") or ev["by"], activity(ev), None, None, ev)
        return {"issue": self.issue_view(ev["id"])}

    def sees(self, x: str, m: dict) -> bool:
        """m is in x's world: x wrote it, it is for x, or it's in a channel or an issue x is in."""
        where = self.channels.get(m["conv"]) or {}
        issue = self.board.get(m["conv"]) or {}
        return (m["author"] == x or x in self.recipients[m["id"]] or x in where.get("members", ())
                or x in issue.get("subscribers", ()))

    def unread(self, x: str, m: dict) -> bool:
        return m["author"] != x and m["seq"] > self.marks.get((x, m["conv"]), 0) and self.sees(x, m)

    def undelivered(self, x: str) -> list:
        """The messages for x that no turn or delivery has shown, less those x has read."""
        return [m for m in self.inboxes.get(x, ()) if m["seq"] > self.marks.get((x, m["conv"]), 0)]

    def issue_view(self, issue_id: str) -> dict:
        i = self.board[issue_id]
        return {**i, "assignees": list(i["assignees"]), "subscribers": list(i["subscribers"])}

    def channel_view(self, channel_id: str) -> dict:
        c = self.channels[channel_id]
        return {**c, "members": list(c["members"])}

    def member_view(self, x: str) -> dict:
        m = self.members[x]
        view = {"id": m["id"], "name": m["name"], "kind": m["kind"]}
        return {**view, **({"operator": True} if m.get("operator") else {}),
                **({"removed": True} if m.get("removed") else {})}

    def mark_read(self, x: str, shown: list):
        """Read marks for the messages shown to x. A conversation's mark moves only up to its first unread message
        that was not shown, so a delivery never hides a message it left out."""
        ids = {m["id"] for m in shown}
        for conv in dict.fromkeys(m["conv"] for m in shown):
            upto = 0
            for m in self.convs[conv]:
                if self.unread(x, m):
                    if m["id"] not in ids:
                        break
                    upto = m["seq"]
            if upto > self.marks.get((x, conv), 0):
                self.append({"type": "read", "member": x, "conv": conv, "upto": upto})

    def find_issue(self, req: dict) -> str:
        i = req.get("id")
        if not isinstance(i, str) or i.strip() not in self.board:
            raise NotFound(f"there is no issue {i}; see `swarmory issues`")
        return i.strip()

    # ---- the API verbs

    def auth(self, token: str):
        digest = sha(token)
        with self.lock:
            return next((x for x, m in self.members.items()
                         if hmac.compare_digest(digest, m.get("token_sha256", ""))), None)

    def worker(self, who: str, digest: str):
        """The server is starting `who`'s worker with a new token, which replaces the one `who` held."""
        with self.lock:
            self.append({"type": "worker", "member": who, "token_sha256": digest})

    def me(self, who: str, req: dict) -> dict:
        with self.lock:
            unread = dict(Counter(m["conv"] for m in self.order if self.unread(who, m)))
        return {"me": self.member_view(who), "unread": unread, "issues": self.issues(who, {"mine": True})["issues"]}

    def who(self, who: str, req: dict) -> dict:
        with self.lock:
            return {"members": [self.member_view(x) for x in self.members],
                    "channels": [self.channel_view(c) for c in self.channels]}

    def read(self, who: str, req: dict) -> dict:
        limit = int(number(req, "limit", 100, 1, 1000))
        with self.lock:
            conv = self.resolve(who, req["conv"]) if req.get("conv") else None
            pool = self.convs.get(conv, []) if conv else self.order
            if req.get("all"):  # a look back at history, which leaves the read marker where it is
                return {"messages": [m for m in pool if not self.hidden(who, m["conv"])][-limit:], "more": 0}
            unread = [m for m in pool if self.unread(who, m)]
            got, more = unread[:limit], max(0, len(unread) - limit)
            upto = {m["conv"]: m["seq"] for m in got}  # ascending, so the last one per conv is its highest
            for c, s in upto.items():
                if s > self.marks.get((who, c), 0):
                    self.append({"type": "read", "member": who, "conv": c, "upto": s})
            return {"messages": list(got), "more": more}

    def send(self, who: str, req: dict) -> dict:
        files, nonce = req.get("files") or [], string(req, "nonce", 64, False)
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            raise ValueError("files is a list of file ids, like f3: `swarmory send` uploads them for you")
        text = string(req, "text", 20000) if req.get("text") or not files else ""  # a file can go without words
        with self.lock:
            unknown = [f for f in files if f not in self.files]
            if unknown:
                raise ValueError(f"there is no file {unknown[0]}: upload it first")
            hit = self.seen(who, nonce, "message")
            if hit:
                return {"message": self.messages[hit]}
            conv = self.resolve(who, req.get("to"), write=True)
            reply = req.get("reply_to")
            if reply is not None and (not isinstance(reply, str) or reply not in self.messages
                                      or self.messages[reply]["conv"] != conv):
                raise ValueError(f"{reply} is not a message in {conv}")
            return {"message": self.post(conv, who, text, reply, nonce, files=files)}

    def upload(self, who: str, name: str, size: int, place) -> dict:
        """A file: place(id) moves its bytes to files/<id>, then the upload is logged. Its id is f1, f2 and so on."""
        with self.lock:
            fid = f"f{self.fcount + 1}"
            place(fid)
            self.append({"type": "file", "id": fid, "name": name, "size": size, "by": who})
            return {"file": self.files[fid]}

    def may_get(self, who: str, fid: str) -> bool:
        """`who` uploaded the file (or it is the artifacts of a job `who` asked for), it was sent in a conversation
        `who` may read, or `who` is the operator."""
        with self.lock:
            f = self.files.get(fid)
            return bool(f) and (f["by"] == who or self.members[who].get("operator")
                                or any(not self.hidden(who, c) for c in self.shared.get(fid, ())))

    def channel(self, who: str, req: dict) -> dict:
        name = string(req, "name", 41).strip().lstrip("#")
        about = string(req, "about", 200, False) or ""
        nonce = string(req, "nonce", 64, False)
        with self.lock:
            hit = self.seen(who, nonce, "channel")
            if hit:
                return {"channel": self.channel_view(hit)}
            if not CHANNEL.fullmatch(name) or self.member(name) or re.fullmatch(r"i\d+", name):
                raise ValueError(f"{name} can't be a channel name: use a-z, 0-9 and -, up to 40, not a member's "
                                 "name or an issue id")
            if name in self.channels:
                raise ValueError(f"#{name} already exists: swarmory join {name}")
            self.append({"type": "channel", "id": name, "about": about, "by": who, "nonce": nonce})
            return {"channel": self.channel_view(name)}

    def join(self, who: str, req: dict, leave: bool = False) -> dict:
        """Adds `who` to a channel's members, or to an issue's subscribers, whom its comments reach. With
        `leave`, removes them."""
        with self.lock:
            name = string(req, "name", 41).strip().lstrip("#")
            issue = name in self.board
            if not issue and name not in self.channels:
                raise NotFound(f"there is no channel or issue {name}; see `swarmory who` or `swarmory issues`")
            group = self.board[name]["subscribers"] if issue else self.channels[name]["members"]
            if (who in group) == leave:  # joining while out, or leaving while in
                self.append({"type": "leave" if leave else "join", "id": name, "member": who})
            return {"issue": self.issue_view(name)} if issue else {"channel": self.channel_view(name)}

    def leave(self, who: str, req: dict) -> dict:
        return self.join(who, req, leave=True)

    def issues(self, who: str, req: dict) -> dict:
        status = req.get("status")
        if status is not None and status not in STATUSES:
            raise ValueError(f"status is one of {', '.join(STATUSES)}")
        with self.lock:
            return {"issues": [self.issue_view(i["id"]) for i in reversed(self.board.values())
                               if (i["status"] == status if status else i["status"] in ("todo", "doing"))
                               and (not req.get("mine") or who in i["assignees"])]}

    def issue(self, who: str, req: dict) -> dict:
        with self.lock:
            i = self.find_issue(req)
            return {"issue": self.issue_view(i), "messages": list(self.convs.get(i, []))}

    def new(self, who: str, req: dict) -> dict:
        title, nonce = string(req, "title", 200).strip(), string(req, "nonce", 64, False)
        body = req.get("body") or ""
        if not isinstance(body, str) or len(body) > 20000:
            raise ValueError("body must be up to 20000 characters of text")
        with self.lock:
            hit = self.seen(who, nonce, "issue")
            if hit:
                return {"issue": self.issue_view(hit)}
            assignees = self.names(who, req.get("assignees") or [])
            return self.change({"type": "issue", "id": f"i{self.icount + 1}", "title": title, "body": body,
                               "author": who, "assignees": assignees, "nonce": nonce})

    def assign(self, who: str, req: dict) -> dict:
        nonce = string(req, "nonce", 64, False)
        with self.lock:
            hit = self.seen(who, nonce, "assign")
            if hit:
                return {"issue": self.issue_view(hit)}
            i = self.find_issue(req)
            now = self.board[i]["assignees"]
            if req.get("if_unassigned") and now:
                raise ValueError(f"{i} is already assigned to {', '.join(now)}")
            add = [x for x in self.names(who, req.get("add") or []) if x not in now]
            remove = [x for x in self.names(who, req.get("remove") or []) if x in now and x not in add]
            if not add and not remove:
                return {"issue": self.issue_view(i)}
            return self.change({"type": "assign", "id": i, "add": add, "remove": remove, "by": who, "nonce": nonce})

    def move(self, who: str, req: dict) -> dict:
        status, nonce = req.get("status"), string(req, "nonce", 64, False)
        if status not in STATUSES:
            raise ValueError(f"status is one of {', '.join(STATUSES)}")
        note = string(req, "note", 20000, False)
        with self.lock:
            hit = self.seen(who, nonce, "move")
            if hit:
                return {"issue": self.issue_view(hit)}
            i = self.find_issue(req)
            return self.change({"type": "move", "id": i, "status": status, "note": note, "by": who, "nonce": nonce})

    # ---- the operator's changes to the team, which the server posts in general

    def announce(self, text: str, nonce: str = None) -> bool:
        """Posts `text` in general as the server. With a nonce, only once in the run: False if it was posted before."""
        with self.lock:
            if self.seen(SYSTEM, nonce, "message"):
                return False
            self.post("general", SYSTEM, text, None, nonce)
            return True

    def add(self, members: list):
        """New agents, each in general."""
        with self.lock:
            for m in members:
                self.append({"type": "member", "member": m})
                self.append({"type": "join", "id": "general", "member": m["id"]})

    def remove(self, ids: list) -> list:
        """Takes members off the team, and off the issues assigned to them, which go back to todo when no one is left
        on them: (the issue, its title) of each."""
        freed = []
        with self.lock:
            for x in ids:
                self.append({"type": "removed", "member": x})
                for i in self.board.values():
                    if x in i["assignees"] and i["status"] in ("todo", "doing"):
                        self.change({"type": "assign", "id": i["id"], "add": [], "remove": [x], "by": SYSTEM})
                        if i["status"] == "doing" and not i["assignees"]:
                            self.change({"type": "move", "id": i["id"], "status": "todo", "by": SYSTEM,
                                         "note": f"{self.members[x]['name']} left the team"})
                        freed.append((i["id"], i["title"]))
        return freed

    def pushed(self, who: str, updates: list):
        """A git push's ref updates, as server.py read them: an event each, for no one."""
        with self.lock:
            for u in updates:
                self.append({"type": "push", "by": who, **u})

    # ---- waiting for messages

    def inbox(self, who: str, req: dict) -> dict:
        """The messages addressed to `who` that no turn or delivery has shown yet, oldest first: what reaches an agent
        between its tool calls. They count as delivered and read."""
        most = int(number(req, "limit", INBOX_MOST, 1, TURN_MOST))
        with self.lock:
            got = self.undelivered(who)[:most]
            if got:
                self.append({"type": "delivered", "member": who, "upto": got[-1]["seq"]})
                self.mark_read(who, got)
            return {"messages": got}

    def hold(self, who: str, end: float, gone) -> bool:
        """Under the lock: holds until messages addressed to `who` come, SETTLE seconds after the first so a burst
        comes as one, or until `end` (time.time()). True if they came. Only the member's newest held call is
        answered, and only while its caller is there to get it (`gone()` says it hung up)."""
        ticket = self.holds[who] = object()
        self.bells[who].notify_all()  # an older call still held ends
        due = None  # when a hold that has heard a message returns
        while True:
            mail, now = self.undelivered(who), time.time()
            due = (due or min(now + SETTLE, end)) if mail else None
            if self.holds[who] is not ticket or (mail and now >= due and gone()):
                return False
            if mail and now >= due:
                return True
            if now >= end:
                return False
            self.bells[who].wait((due or end) - now)

    def news(self, who: str, main) -> dict:
        """Under the lock: what changed since `who`'s last turn, which starts its next turn."""
        since, mail = self.turns.get(who), self.undelivered(who)
        shown = mail[:TURN_MOST]
        self.append({"type": "turn", "member": who, "main": main, "delivered": shown[-1]["seq"] if shown else 0})
        self.mark_read(who, shown)
        channels = Counter(m["conv"] for m in self.order if m["conv"] in self.channels and self.unread(who, m))
        return {"done": True, "messages": shown, "more": len(mail) - len(shown), "channels": dict(channels),
                "since": since, "main": self.turns[who]}

    def wait(self, who: str, req: dict, gone=lambda: False, head=lambda: None) -> dict:
        """`who` has nothing to do until someone writes to it. Holds up to req's timeout (0 by default, at most
        HOLD seconds) for messages addressed to it; returns what changed if they came, else {"done": false}. An agent
        that waits starts its next turn on a message, or IDLE seconds after it began to wait."""
        timeout = number(req, "timeout", 0, 0, HOLD)
        with self.lock:
            if who not in self.waiting:
                self.append({"type": "wait", "member": who})
            ready = self.hold(who, time.time() + timeout, gone) or (bool(self.undelivered(who)) and not gone())
            if not ready:
                return {"done": False, "idle": self.waiting[who] + IDLE}
            ticket = self.holds.get(who)
        main = head()
        with self.lock:
            if self.holds.get(who) is not ticket or gone():
                return {"done": False, "idle": self.waiting.get(who, time.time()) + IDLE}
            return self.news(who, main)

    def next(self, who: str, req: dict, gone=lambda: False, head=lambda: None) -> dict:
        """An agent's worker, between two turns: the next turn starts at once, unless the agent waits; then on a
        message for it, or IDLE seconds after it began to wait. Holds up to req's timeout (at most HOLD seconds), then
        answers {"done": false}; call again."""
        end, ticket = time.time() + number(req, "timeout", HOLD, 0, HOLD), None
        with self.lock:
            if who in self.waiting:
                start = self.waiting[who] + IDLE
                if not self.hold(who, min(end, start), gone) and time.time() < start:
                    return {"done": False}
                ticket = self.holds.get(who)
        main = head()
        with self.lock:
            if ticket is not None and (self.holds.get(who) is not ticket or gone()):
                return {"done": False}
            return self.news(who, main)



