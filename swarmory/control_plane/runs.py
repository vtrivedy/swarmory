"""The runs one server holds. A run is a folder, DATA/runs/RUN, that holds everything about it: config.json, its log
(log.jsonl, the team's and its jobs' events), its ledger, its repo (work.git), shared files, traces, its jobs' folders,
and each local agent's home under members/. Copying a run's folder into another server's runs/ and restarting that
server serves the run.

Run is one run as the server holds it, read back from its folder: its Team, Jobs and Proxy, who a token is, what its
workers should be, and what the viewer reads. Runs loads each run once, at the server's start if it is running and on
demand if it has ended, and makes new ones (new_run checks a `start` request)."""
import hashlib
import hmac
import io
import itertools
import json
import os
import re
import secrets
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from swarmory import harness
from swarmory.harness.base import APIS

from . import traces
from .jobs import Jobs, git, limits
from .policy import Principal, sha
from .prompt import STAGGER
from .proxy import Proxy, money
from .supervisor import Want
from .team import NotFound, Team, replay

NAMES = (
    "ada grace alan edsger barbara donald ken dennis margaret frances niklaus leslie radia hedy john tony "
    "lynn ivan butler jim linus guido bjarne brian rob robin claude kurt alonzo emmy sophie evelyn annie jean "
    "kathleen karen shafi adele mary dorothy katherine joan anita fei daphne cynthia nancy jeannette susan "
    "lise marie rosalind vera cecilia maria gerty irene barbro stephanie ruth betty marlyn kay adi whit ralph "
    "ron len silvio manuel leonard dana michael richard juris peter fernando douglas vint bob tim charles "
    "herbert allen marvin seymour judea geoffrey yann yoshua andrew demis ilya andrej oriol jeff sanjay luis "
    "eric paul larry sergey bill steve gordon robert jack federico carver dave lotfi edgar ted stuart pat raj "
    "butch amir ed joseph william martin ole kristen wesley maurice david heinz konrad tommy howard presper "
    "clifford vannevar norbert warren walter frank oliver satya sundar lisa megan chris simon philip xavier "
    "matei ion scott burton jon van danny alvy edwin henry jensen lynne mitchell gary gene fred harlan watts "
    "kent ward erich grady ivar rebecca yukihiro rasmus anders james brendan ryan graydon rich jose joe "
    "simone roberto martha carl tracy sally mae valentina yuri neil buzz sunita kalpana chien emilie hypatia "
    "ida rita tu jane rachel sylvia wangari "
).split()  # agents' names, in order; a team bigger than the list goes round again as ada2, grace2, ...
RESERVED = ("all", "channel", "here", "general", "me")  # words for a group, a channel or the caller: no member's id
IMAGE_MOST = 32 << 10  # bytes of a mission's image.sh, which goes in the run's config
FEED_MOST = 4 << 20  # bytes of each log per /feed answer; the viewer asks again for the rest
TRACE_EVENTS = 3000  # events per /trace answer; the viewer asks again for the rest
SESSION = re.compile(r"session-(\d+)\.jsonl")
COMMITS_MOST, PATCH_MOST = 500, 512 << 10
FORMAT = "%H%x1f%ce%x1f%an%x1f%ct%x1f%s%x1f"  # then the body, or in a log the files
SESSIONS = {}  # session file -> (its size, its events), the CACHED most recently read: a file is read once per size
CACHED = 64
PRE_RECEIVE = """#!/bin/sh
set -f
while read -r old new ref; do
  ok=
  for p in ${SWARMORY_PUSH-refs/heads/main refs/submit/*}; do case "$ref" in $p) ok=1 ;; esac; done
  [ -n "$ok" ] || { echo "This server refuses $ref from you. You may push: ${SWARMORY_PUSH-refs/heads/main}."; exit 1; }
done
"""
READS = threading.Lock()  # around SESSIONS, which viewers' requests share
WARN = 600  # seconds before the deadline that the team is told to push its work
SPENT = 0.9  # the share of the budget at which the team is told to push its work


def view_token(operator: str, run: str) -> str:
    """A run's watch link token, made from the operator token, which only the operator holds: the server keeps its
    digest, and gives the link again to whoever shows the operator token."""
    return hmac.new(operator.encode(), f"view {run}".encode(), hashlib.sha256).hexdigest()[:32]


def undigested(x, mask=str):
    """x without any token digest, and with `mask` (a traces.Masker's `known`) over each string, so a token pasted
    into a message is hidden too: what leaves the server for a viewer or an export."""
    if isinstance(x, dict):
        return {k: undigested(v, mask) for k, v in x.items() if not k.endswith("sha256")}
    return [undigested(v, mask) for v in x] if isinstance(x, list) else mask(x) if isinstance(x, str) else x


def parse(text: str):
    """A commit git printed in FORMAT, and what follows it. worker/agent.py commits as <id>@swarmory.local."""
    sha_, email, name, at, subject, rest = text.split("\x1f", 5)
    local, _, domain = email.partition("@")
    return {"sha": sha_, "author": local if local and domain == "swarmory.local" else name, "at": int(at),
            "subject": subject}, rest


class Run:
    def __init__(self, folder: Path, litellm, url: str):
        """`url` is the server's own address: Jobs refuses a job whose trials could not reach it."""
        self.id, self.dir, self.repo = folder.name, folder, folder / "work.git"
        self.config = json.loads((folder / "config.json").read_text())
        self.jobs = Jobs(self.repo, bool(self.config.get("jobs")), self.config["deadline"], url)
        self.team = self.jobs.team = Team(folder, self.jobs.apply)
        self.team.digests.add(self.config["view_sha256"])
        self.proxy = Proxy(folder, os.environ, self.config.get("budget"), litellm,
                           bool(self.config.get("subscription")))
        self.mask = traces.masker(os.environ, self.team.digests)
        self.operator = self.config["operator"]
        self.agents = agent_ids(self.config)
        self.changes = threading.Lock()  # around the operator's changes to config.json
        self.tip = (0.0, None)  # when main's head was last read, and what it was
        self.history = (None, [])  # main's head and /commits' answer for it
        self.uploads = threading.Lock()

    def state(self, now: float = None) -> str:
        return state(self.config, now)

    def refs(self, who):
        """Prototype of the git piece's choice: (patterns `who` may push, refs it may fetch or None for all)."""
        tags = self.team.members[who].get("tags") or ()
        squad = next((t[6:] for t in tags if t.startswith("squad:")), None)
        if not squad:
            return ("refs/heads/main", "refs/submit/*"), None
        return (f"refs/heads/squad/{squad}",), ("refs/heads/main", f"refs/heads/squad/{squad}")

    def principal(self, token: str, grace: float = 0):
        """Whose token this is in this run (policy.Principal), or None. Only a running run takes its members', jobs'
        and trials' tokens, and a member's for `grace` seconds after it ended (a worker's last trace upload); its view
        token works always. The operator's is the server's, checked before this."""
        running = self.state() == "running"
        if running or time.time() - (self.config.get("stopped") or self.config["deadline"]) < grace:
            member = self.team.auth(token)
            if member:
                return Principal("member", member)
            held = running and self.jobs.auth(token)
            if held:
                return Principal(held[0], held[1]["by"], held[1]["id"])
        return Principal("view") if hmac.compare_digest(sha(token), self.config["view_sha256"]) else None

    def lead(self) -> str | None:
        """The agent that starts alone while the others wait (STAGGER), or None once they all may start: an agent
        starts on an empty board after the first, which would split the same work again."""
        first = self.agents[0] if len(self.agents) > 1 and STAGGER else None
        since = self.team.started.get(first)
        alone = first and not self.team.board and set(self.team.started) <= {first}
        return first if alone and (since is None or time.time() - since < STAGGER) else None

    def wanted(self, now: float) -> list:
        """What should run for this run now (supervisor.plan's input): each agent's worker while the run is running and
        its repo has a main, only the lead() while there is one, and a worker for each job it holds or may claim."""
        up, lead = self.state(now) == "running" and self.head() is not None, self.lead()
        agents = [Want(f"{self.id}/agent/{a}", up and lead in (None, a), self.team.started.get(a))
                  for a in self.agents]
        return agents + [Want(f"{self.id}/job/{j}", self.state(now) == "running", since)
                         for j, since in self.jobs.workers()]

    def warn(self, now: float):
        """Tells the team once, WARN seconds before the deadline, and once at SPENT of each budget the run has, to push
        its work: an agent cut off by the deadline or the budget loses what it hasn't pushed."""
        if self.state(now) != "running":
            return
        left = self.config["deadline"] - now
        if left <= WARN and self.config["deadline"] - self.config["created"] > 2 * WARN:
            self.team.announce(f"@all The run ends in about {max(1, round(left / 60))} minutes. Commit, pull and push "
                               "your work now, and comment on its issue: what is not on main when the run ends is "
                               "lost.",
                               f"deadline {self.config['deadline']}")
        budget = self.config.get("budget")
        spent = sum(u.get("usd") or 0 for u in self.proxy.totals().values()) if budget else 0
        if budget and spent >= SPENT * budget:
            self.team.announce(f"@all The run has spent {money(spent)} of its {money(budget)} budget, and model calls "
                               "stop when it is spent. Commit, pull and push your work now, and comment on its issue.",
                               f"budget {budget}")

    def change(self, **values):
        """Sets config.json's `values`."""
        with self.changes:
            self.config.update(values)
            write(self.dir / "config.json", self.config)

    def set_budget(self, budget, priced):
        """The run's budget is now `budget` dollars, or None for no limit; the team hears it."""
        if budget is not None:
            unpriced([m for m in self.config["members"] if m["id"] in self.agents], priced)
        self.change(budget=budget)
        self.proxy.budget = budget
        spent = sum(u.get("usd") or 0 for u in self.proxy.totals().values())
        said = money(budget) if budget is not None else "none"
        self.team.announce(f"@all The operator set the run's budget to {said} (spent so far: {money(spent)}).")

    def add(self, members: list):
        """Adds agents to the team: the supervisor starts them, and the team hears who they are."""
        with self.changes:
            self.config["members"] = self.config["members"] + members
            write(self.dir / "config.json", self.config)
            self.agents = agent_ids(self.config)
        self.team.add(members)
        self.team.announce("@all New on the team: " + "; ".join(
            f"{m['name']} ({m['id']}), {m['harness']}" + (f", role: {m['role']}" if m.get("role") else "")
            for m in members) + ". Welcome them, and point them at open issues.")

    def remove(self, ids: list):
        """Takes agents off the team: the supervisor stops them, their tokens stop working, and their open issues are
        unassigned for the team to pick up."""
        now = time.time()
        with self.changes:
            self.config["members"] = [{**m, "removed": now} if m["id"] in ids else m for m in self.config["members"]]
            write(self.dir / "config.json", self.config)
            self.agents = agent_ids(self.config)
        freed = self.team.remove(ids)
        names = ", ".join(f"{self.team.members[x]['name']} ({x})" for x in ids)
        self.team.announce(f"@all The operator took {names} off the team." + (
            " Their open issues are unassigned: " + "; ".join(f"{i} {t}" for i, t in freed) + "." if freed else ""))

    def stop(self):
        """Marks the run stopped, for good: the supervisor stops its workers, and the run stays readable."""
        if not self.config.get("stopped"):
            self.change(stopped=time.time())

    def status(self, live: frozenset) -> dict:
        """The `status` verb's answer: the run's state, and each member's worker, sessions and what its model calls
        used."""
        by, now, state_ = self.proxy.totals(), time.time(), self.state()
        members = []
        for m in self.config["members"]:
            agent, name = m["kind"] == "agent", f"{self.id}/agent/{m['id']}"
            worker = None if not agent else "removed" if m.get("removed") else "running" if name in live \
                else state_ if state_ != "running" else "waiting for main" if self.head() is None \
                else "waiting for lead" if self.lead() not in (None, m["id"]) else "down"
            members.append({
                "id": m["id"], "harness": m.get("harness", ""), "model": (m.get("model") or "default") if agent
                else "a person", "worker": worker, "starts": self.team.starts[m["id"]] if agent else None,
                "sessions": len(list((self.dir / "traces" / m["id"]).glob("session-*.jsonl"))) if agent else None,
                "used": by.get(m["id"])})
        return {"run": self.id, "mission": self.config["mission"], "state": state_, "deadline": self.config["deadline"],
                "minutes_left": max(0, int(self.config["deadline"] - now) // 60), "budget": self.config.get("budget"),
                "members": members}

    # ---- the repo, and what the viewer reads

    def head(self):
        """main's head, read at most once a second: the viewer polls, and the supervisor asks every tick."""
        if time.monotonic() - self.tip[0] >= 1:
            sha_ = git(self.repo, "rev-parse", "-q", "--verify", "refs/heads/main^{commit}", check=False)
            self.tip = (time.monotonic(), sha_.decode().strip() if sha_ else None)
        return self.tip[1]

    def commits(self, _) -> dict:
        """main's history, newest first, each commit with its files' lines added and deleted (None for a binary)."""
        head = self.head()
        if self.history[0] != head:
            log = git(self.repo, "log", "--no-renames", f"-{COMMITS_MOST}", "--numstat", "--format=%x1e" + FORMAT,
                           head) if head else b""
            out = []
            for text in log.decode(errors="replace").split("\x1e")[1:]:
                c, stats = parse(text)
                files = [line.split("\t", 2) for line in stats.splitlines() if line]
                out.append({**c, "files": [{"path": p, "add": None if a == "-" else int(a),
                                            "del": None if d == "-" else int(d)} for a, d, p in files]})
            self.history = (head, out)
        return {"commits": self.history[1]}

    def commit(self, q: dict) -> dict:
        want = q.get("sha", [""])[0].lower()
        if not re.fullmatch(r"[0-9a-f]{7,40}", want):
            raise ValueError("sha must be 7 to 40 hex characters")
        full, main = git(self.repo, "rev-parse", "-q", "--verify", want + "^{commit}", check=False), self.head()
        full = full and full.decode().strip()
        if not full or not main or git(self.repo, "merge-base", "--is-ancestor", full, main, check=False) is None:
            raise NotFound(f"{want} is not a commit on main")
        c, body = parse(git(self.repo, "show", "-s", "--format=" + FORMAT + "%b", full).decode(errors="replace"))
        patch = git(self.repo, "show", "--format=", "--patch", "--no-renames", full, most=PATCH_MOST + 1)
        truncated = len(patch) > PATCH_MOST
        if truncated:
            patch = patch[:patch.rfind(b"\n", 0, PATCH_MOST) + 1]
        return {**c, "body": body.strip(), "patch": patch.decode(errors="replace"), "truncated": truncated}

    def feed(self, q: dict) -> dict:
        """What the viewer polls: the run, main's head, and the whole lines of the log and the ledger after the byte
        offsets it has; `more` if there are more lines than one answer holds."""
        out = {"run": {k: self.config[k] for k in ("run", "mission", "deadline", "budget")}, "now": time.time(),
               "live": self.state() == "running", "head": self.head(), "next": {}, "more": False}
        for name in ("log", "ledger"):
            start = q.get(name, ["0"])[0]
            if not start.isdigit():
                raise ValueError(f"{name} must be a byte offset")
            path, start = self.dir / f"{name}.jsonl", int(start)
            with open(path, "rb") if path.exists() else io.BytesIO() as f:
                f.seek(start)
                chunk = f.read(FEED_MOST + 1)
            out["more"] |= len(chunk) > FEED_MOST
            chunk = chunk[:chunk.rfind(b"\n", 0, FEED_MOST) + 1]  # not a line still being written
            out[name], out["next"][name] = [undigested(json.loads(line), self.mask.known)
                                            for line in chunk.splitlines()], start + len(chunk)
        return out

    def session_events(self, path: Path) -> list:
        """A session file's events (traces.py), masked, read again only once the file has grown."""
        with READS:
            size = path.stat().st_size
            hit = SESSIONS.pop(path, None)
            if not hit or hit[0] != size:
                at = path.with_suffix(".at")
                arrivals = [(int(o), float(s)) for o, s in (x.split() for x in at.read_text().splitlines() if x)] \
                    if at.exists() else []
                hit = (size, traces.events(path.read_bytes()[:size], arrivals, self.mask))
            SESSIONS[path] = hit  # last: the most recently read
            while len(SESSIONS) > CACHED:
                SESSIONS.pop(next(iter(SESSIONS)))
            return hit[1]

    def keep_trace(self, member: str, name: str, at: int, body: bytes) -> tuple:
        """Adds `body` to the server's copy of a member's session file or agent.log, and notes beside it (NAME.at) the
        offset and the time it came, which session_events reads. Only if the copy is `at` bytes long: (whether it was
        added, the copy's size now)."""
        path = self.dir / "traces" / member / name
        with self.uploads:
            size = path.stat().st_size if path.exists() else 0
            if size != at:
                return False, size
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "ab") as f:
                f.write(body)
            with open(path.with_suffix(".at"), "a") as f:
                f.write(f"{size} {time.time():.3f}\n")
        return True, size + len(body)

    def trace(self, q: dict) -> dict:
        """One agent's trace for the viewer: the events after `after` (SESSION:INDEX, a session file and an event's
        place in it), each with its session and index; `more` if there are more than one answer holds."""
        member = q.get("member", [""])[0]
        if member not in self.agents:
            raise NotFound(f"no agent {member!r}")
        after = q.get("after", [""])[0]
        name, _, index = after.rpartition(":")
        since = (int(SESSION.fullmatch(name)[1]), int(index)) if SESSION.fullmatch(name) and index.isdigit() else (0, 0)
        folder = self.dir / "traces" / member
        files = sorted((int(m[1]), f) for f in folder.glob("session-*.jsonl") if (m := SESSION.fullmatch(f.name)))
        out, more = [], False
        for n, f in files:
            if n < since[0] or more:
                continue
            evs = self.session_events(f)
            for i in range(since[1] if n == since[0] else 0, len(evs)):
                if len(out) == TRACE_EVENTS:
                    more = True
                    break
                out.append({**evs[i], "session": n, "i": i})
                after = f"{f.name}:{i + 1}"
        return {"member": member, "events": out, "after": after, "more": more, "now": time.time(),
                "live": self.state() == "running"}

    def export(self, into) -> None:
        """Writes the run's records to the open file `into` as a tar.gz under RUN/: its config, log and ledger, shared
        files, each agent's sessions and agent.log, and work.bundle, a git bundle of every ref. It holds no token and
        no digest: they are left out of the config and the log, and a session's text is masked as the viewer's is."""
        with tarfile.open(fileobj=into, mode="w:gz") as tar:
            def add(name: str, data: bytes):
                info = tarfile.TarInfo(f"{self.id}/{name}")
                info.size, info.mtime, info.mode = len(data), int(time.time()), 0o644
                tar.addfile(info, io.BytesIO(data))

            add("config.json", json.dumps(undigested(self.config), indent=1).encode())
            log = []
            replay(self.dir / "log.jsonl", log.append, repair=False)
            add("log.jsonl", "".join(json.dumps(undigested(e, self.mask.known)) + "\n" for e in log).encode())
            for f in sorted(self.dir.glob("ledger.jsonl")) + sorted((self.dir / "files").glob("f*")):
                tar.add(f, f"{self.id}/{f.relative_to(self.dir)}")
            # from the copies agents upload (traces/), which the server has on every backend: on Modal it does not
            # see what an agent's sandbox writes to the Volume
            traces_ = self.dir / "traces"
            named = [(f"members/{f.parent.name}/sessions/{f.name}", f) for f in traces_.glob("*/session-*.jsonl")]
            named += [(f"members/{f.parent.name}/agent.log", f) for f in traces_.glob("*/agent.log")]
            for name, f in sorted(named):
                add(name, "\n".join(map(self.mask, f.read_text(errors="replace").split("\n"))).encode())
            with tempfile.TemporaryDirectory() as tmp:
                if git(self.repo, "bundle", "create", f"{tmp}/work.bundle", "--all", check=False) is not None:
                    tar.add(f"{tmp}/work.bundle", f"{self.id}/work.bundle")


def agent_ids(config: dict) -> list:
    """The run's agents now, in the order they joined: not those the operator took off the team."""
    return [m["id"] for m in config["members"] if m["kind"] == "agent" and not m.get("removed")]


def state(config: dict, now: float = None) -> str:
    """running, ended (its deadline has passed) or stopped (the operator stopped it)."""
    return "stopped" if config.get("stopped") else "ended" if (now or time.time()) >= config["deadline"] else "running"


def write(path: Path, obj):
    """Whole or not at all, and readable by no one else: a run's folder holds what an agent wrote."""
    with os.fdopen(os.open(f"{path}.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(f"{path}.tmp", path)


def new_run(req: dict, backend, env, operator: str, owner, priced=None) -> tuple:
    """(the config of a new run, its view token) for a `start` request, checked. `operator` is the token of whoever
    asked, which the view token is made from. `owner` and `priced` are agents()'s. A ValueError says what is wrong in
    one line."""
    mission, (hours, budget) = req.get("mission"), limits(req)
    grants, allow = req.get("grant") or [], req.get("allow") or []
    me, jobs, subscription = req.get("me") or "operator", req.get("jobs") or False, bool(req.get("subscription"))
    image = req.get("image") or None
    if image is not None and (not isinstance(image, str) or len(image.encode()) > IMAGE_MOST):
        raise ValueError(f"image is the mission's image.sh: a script of at most {IMAGE_MOST // 1024} KB")
    if not isinstance(mission, str) or not 0 < len(mission) <= 100:
        raise ValueError("mission is the mission's name, 1 to 100 characters")
    if not all(isinstance(x, list) and all(isinstance(s, str) for s in x) for x in (grants, allow)) \
            or not isinstance(me, str) or not isinstance(jobs, bool):
        raise ValueError("grant and allow are lists of names, me a name and jobs true or false")
    op = re.sub(r"[^a-z]", "", me.lower())  # the operator keeps their own name: no agent is given it by default
    if not op or op in RESERVED:
        raise ValueError(f"Your id {op!r} is empty or a reserved word. It comes from your git user.name.")
    members = agents(req.get("team"), backend, env, owner, priced, hours=hours, budget=budget,
                     subscription=subscription, grants=grants, allow=allow, taken={op})
    members.append({"id": op, "name": op.title(), "kind": "human", "operator": True})
    now = time.time()  # the id's date and time are UTC, wherever the server runs: an id has no room for a zone
    rid = f"{re.sub(r'[^A-Za-z0-9-]', '-', mission)}-{time.strftime('%m%d-%H%M', time.gmtime(now))}-" \
          f"{secrets.token_hex(2)}"
    view = view_token(operator, rid)
    return {"run": rid, "mission": mission, "created": now, "deadline": int(now + hours * 3600), "members": members,
            "operator": op, "view_sha256": sha(view), "channels": [{"id": "general", "about": "The whole team."}],
            "budget": budget, "jobs": jobs, "grants": grants, "allow": allow, "subscription": subscription,
            "image": image}, view


def agents(team, backend, env, owner, priced, *, hours: float, budget, subscription: bool, grants: list, allow: list,
           taken: set) -> list:
    """The members a `start` or an `add` request's team makes, checked: each agent's id is its name's, else the next
    spare one of NAMES, and none is one of `taken`. They run `hours` more, under `budget`. `owner` gives the provider
    that serves a model, whose key an agent on that model needs. `priced` says whether the server's price table lists
    a model: a budgeted run's gateway refuses every call to a model it can't price."""
    if not isinstance(team, list) or not team:
        raise ValueError("A team is a list of at least one agent.")
    built_in, command = [h.name for h in harness.HARNESSES], harness.COMMAND_FIELDS
    for n, m in enumerate(team, 1):
        if (not isinstance(m, dict) or not isinstance(m.get("harness"), str)
                or m.keys() - {"harness", "model", "role", "name", "args", "tags", *command}
                or not all(isinstance(m.get(k) or "", str) for k in ("model", "role", "name", *command))
                or not isinstance(m.get("args") or [], list)
                or not all(isinstance(a, str) and "\n" not in a for a in m.get("args") or [])):
            raise ValueError(f"team entry {n}: want a harness, and only model, role, name, args (a list of one-line "
                             f"strings), and for a harness other than {' or '.join(built_in)}: {', '.join(command)}.")
        if m["harness"] in built_in and m.keys() & set(command):
            raise ValueError(f"team entry {n}: {m['harness']} is built in, so it takes no {', '.join(command)}.")
        if m["harness"] not in built_in and not all("{prompt}" in (m.get(k) or "") for k in ("run", "resume")):
            raise ValueError(f"team entry {n}: {m['harness']} is not built in ({', '.join(built_in)}), so it needs "
                             "run and resume, each a command with {prompt} in it.")
        model, h = m.get("model") or "", harness.of(m)
        if m.get("args") and not h.takes_args:
            raise ValueError(f"team entry {n}: {m['harness']} takes no args.")
        if h.provider is None and (model.partition("/")[0] not in APIS or not owner(model)):
            raise ValueError(f"team entry {n}: {m['harness']} needs a model the gateway knows, named with its "
                             f"provider ({', '.join(APIS)}), such as anthropic/claude-sonnet-5-5.")
    line = lambda m, k: " ".join((m.get(k) or "").split()) or None  # noqa: E731
    named = [line(m, "name") and re.sub(r"[^a-z0-9-]", "", line(m, "name").lower()) for m in team]
    spare = (i for r in itertools.count(1) for n in NAMES for i in [n if r == 1 else f"{n}{r}"]
             if i not in named and i not in taken)
    ids = [next(spare) if i is None else i for i in named]
    for m, i in zip(team, ids):
        if m.get("name") and (not i[:1].isalpha() or i in RESERVED or re.fullmatch(r"i\d+", i)):
            raise ValueError(f"The name {m['name']!r} makes the id {i!r}, but an id starts with a letter (so it can be "
                             f"@mentioned) and is not {', '.join(RESERVED)} or i<number> (an issue).")
        if i in taken:
            raise ValueError(f"The name {m['name']!r} makes the id {i!r}, which is taken: give another name.")
    if len(set(ids)) < len(ids):
        raise ValueError(f"Two agents have the id {next(i for i in ids if ids.count(i) > 1)!r}: give them other names.")
    used = list({m["harness"]: harness.of(m) for m in team}.values())
    backend.check(used, grants, allow)
    for m in team if subscription else ():  # a login calls only its own provider: no call is translated
        h, provider = harness.of(m), owner(m.get("model") or None)
        if h.login and provider and provider != h.provider:
            raise ValueError(f"With --subscription, a {h.name} agent calls only {h.provider} models, and "
                             f"{m['model']} is {provider}'s: pick one of {h.provider}'s models, or run with API keys.")
    for h in used if subscription else ():
        if not h.login:
            raise ValueError(f"The team has {h.name} agents, which take no subscription login: run without it.")
        login = env.get(h.login) and h.signin(env[h.login])
        if not login or (login.expires or float("inf")) < time.time() + hours * 3600:
            raise ValueError(f"The team has {h.name} agents, so the server's {h.login} must be {h.login_from}, good "
                             "until the run ends.")
    if budget is not None and priced:
        unpriced(team, priced)
    for m in () if subscription else team:  # else every model call of that agent's fails with the gateway's 503
        key = f"{(owner(m.get('model') or None) or harness.of(m).provider).upper()}_API_KEY"
        if not env.get(key):
            raise ValueError(f"The {m['harness']} agent on {m.get('model') or 'its default model'} needs {key}, which "
                             "the server's environment does not have.")
    return [{"id": i, "name": line(m, "name") or i.title(), "harness": m["harness"], "model": m.get("model") or None,
             "role": line(m, "role"), "args": m.get("args") or [], "kind": "agent",
             **({"tags": m["tags"]} if m.get("tags") else {}),
             **{k: m[k] for k in harness.COMMAND_FIELDS if m.get(k)}} for i, m in zip(ids, team)]


def unpriced(team: list, priced):
    """A ValueError if a budgeted run would have an agent whose model the server can't price: the gateway would refuse
    every call of that agent's, mid-run."""
    for m in team:
        if not m.get("model"):
            raise ValueError(f"The run has a budget, so give the {m['harness']} agent a model, such as "
                             f"{m['harness']}:MODEL, whose price the server can check.")
        if not priced(m["model"]):
            raise ValueError(f"The run has a budget, and the server has no price for {m['model']!r}, so the gateway "
                             "would refuse every call to it. Pick a model LiteLLM's price table lists, or run with "
                             "--budget none.")


class Runs:
    def __init__(self, data: Path, litellm, url: str):
        self.dir, self.litellm, self.url = Path(data) / "runs", litellm, url
        self.dir.mkdir(parents=True, exist_ok=True)
        self.held = {}  # run id: Run
        self.lock = threading.Lock()
        for rid, config in self.configs():
            if state(config) == "running":
                self.get(rid)

    def configs(self) -> list:
        """(run id, config) of every run in the data directory."""
        out = []
        for f in self.dir.glob("*/config.json"):
            try:
                out.append((f.parent.name, json.loads(f.read_text())))
            except (OSError, ValueError):
                pass
        return out

    def get(self, rid: str):
        """The run, loaded from its folder the first time it is asked for, or None if there is none."""
        if not re.fullmatch(r"[\w-]+", rid or ""):
            return None
        with self.lock:
            if rid not in self.held and (self.dir / rid / "config.json").is_file():
                self.held[rid] = Run(self.dir / rid, self.litellm, self.url)
            return self.held.get(rid)

    def loaded(self) -> list:
        with self.lock:
            return list(self.held.values())

    def create(self, config: dict) -> Run:
        """A new run's folder, with its config and an empty repo, loaded."""
        folder = self.dir / config["run"]
        folder.mkdir(mode=0o700)
        write(folder / "config.json", config)
        repo = str(folder / "work.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", repo], check=True)
        # pushes over HTTP, none of which can rewrite history or delete a branch; refs/jobs/<id> pins what a job ran
        # on, and only the server writes it
        for key, value in (("http.receivepack", "true"), ("receive.denyNonFastForwards", "true"),
                           ("receive.denyDeletes", "true"), ("receive.hideRefs", "refs/jobs")):
            subprocess.run(["git", "-C", repo, "config", key, value], check=True)
        hook = folder / "work.git" / "hooks" / "pre-receive"  # work pushed to any other branch is work nobody sees
        hook.write_text(PRE_RECEIVE)
        hook.chmod(0o755)
        return self.get(config["run"])
