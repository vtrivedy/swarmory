"""Jobs: Harbor runs a member asks the run to do on a committed folder of tasks.

A job is a commit, a path and Harbor's parameters. The server checks that the commit is in the run's repo,
records the git tree id of `commit:path`, and pins the commit under refs/jobs/<id>. The submitter sets none of that,
so a result names exactly the content it ran on, and `jobs` finds every job on the same tree. A job worker
(swarmory/worker/job.py) gets its input from `git archive` of that tree. The server's supervisor claims each queued
job and starts its worker. The claim gives the job a tag, which Harbor puts on each sandbox it starts for it, so the
sandboxes a job leaves behind can be ended when it ends.

Harbor runs every job (swarmory/harbor.py). Jobs keeps its events (job, claim, finish, cancel) in the team's log and
posts through the team."""
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

from swarmory import HOURS, harbor

from .policy import sha
from .proxy import money
from .team import NotFound, SYSTEM, number, string

TOKENS = 20_000_000  # tokens in and out one job's trials may use through the gateway
READ_MOST = 1 << 20  # bytes of one input file Harbor's check may read


class Inputs:
    """The committed folder a job runs on, for Harbor's check(): read from the run's repo, never from an upload."""

    def __init__(self, repo: Path, tree: str):
        self.repo, self.tree = repo, tree

    def names(self) -> list:
        """Every file in the folder, as a path inside it."""
        return git(self.repo, "ls-tree", "-r", "-z", "--name-only", self.tree).decode(errors="replace").split("\0")[:-1]

    def read(self, name: str) -> bytes:
        size = git(self.repo, "cat-file", "-s", f"{self.tree}:{name}", check=False)
        if size is None or int(size) > READ_MOST:
            raise ValueError(f"{name} is missing or over {READ_MOST >> 20} MB")
        return git(self.repo, "cat-file", "blob", f"{self.tree}:{name}")


def git(repo: Path, *args, most: int = -1, into=None, check: bool = True):
    """The server's one way to run git on a run's repo: its output, cut at `most` bytes, or written to the open file
    `into`. A failure raises, or with check=False returns None. No HOME, so no one's git config changes what it
    prints."""
    with subprocess.Popen(["git", "-C", str(repo), "-c", "core.quotePath=false", *args],
                          stdout=into or subprocess.PIPE, stderr=subprocess.PIPE,
                          env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")}) as p:
        out = b"" if into else p.stdout.read(most)
        if len(out) == most:
            p.kill()
        said = p.stderr.read().decode(errors="replace").strip()[:300]
    if p.returncode == 0 or len(out) == most:
        return out
    if check:
        raise RuntimeError(f"git {args[0]} failed: {said}")
    return None


def limits(req: dict) -> tuple:
    """(hours, budget) of a `start` or a `job` request, checked: how long the run or the job lasts, HOURS unless it
    says, and the most its model calls may cost in dollars, or None for no limit."""
    hours, budget = req.get("hours", HOURS), req.get("budget")
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < hours <= 24 * 30:
        raise ValueError("hours is a number over 0, and 720 (30 days) at most")
    if budget is not None and (isinstance(budget, bool) or not isinstance(budget, (int, float)) or not budget > 0):
        raise ValueError("budget is a number of dollars over 0")
    return hours, budget


def described(job: dict) -> str:
    params = ", ".join(f"{k} {v}" for k, v in job["params"].items() if v is not None)
    budget = f", up to {money(job['budget'])}" if job.get("budget") is not None else ""
    return f"harbor on {job['path']} at {job['commit'][:7]}" + (f" ({params})" if params else "") + budget


def verdict(job: dict) -> str:
    """A finished job's result, for the message that reports it."""
    o = job["outcome"] or {}
    why = (o.get("error") or "").strip().split("\n")[0]
    head = ("was canceled" if job["status"] == "canceled" else f"failed: {why}" if job["status"] == "failed"
            else f"finished: {o.get('headline') or 'done'}")
    tail = "has the rest" + (", and its artifacts are attached" if job["artifacts"] else "")
    errors = [f"\n- {t['trial']}: {t['error'].splitlines()[0]}" for t in (o.get("summary") or {}).get("each") or ()
              if t.get("error")]
    return f"{head} ({described(job)}). `swarmory job show {job['id']}` {tail}." + "".join(errors)


class Jobs:
    """The run's job queue. Its events live in the team's log: pass `apply` to Team, then set `team`. One method per
    API verb, each taking the acting member's id and the request, as Team's do; policy.py has already
    said the caller may."""

    def __init__(self, repo: Path, enabled: bool, deadline: float, url: str):
        """`enabled`: the run was started with --jobs. No job outlasts `deadline`, the run's. `url` is the server's
        address, by which Harbor's check knows whether trials off this machine reach its model gateway."""
        self.repo, self.enabled, self.deadline, self.url = Path(repo), enabled, deadline, url
        self.team = None
        self.jobs = {}    # job id -> the job, from its job, claim, finish and cancel events
        self.count = 0
        self.tokens = {}  # sha256 of a claimed job's job or spend token -> ("job" or "spend", the job's id)

    def apply(self, ev: dict):
        t = ev["type"]
        if t == "job":
            self.count = max(self.count, int(ev["id"][1:]))
            asked = ("id", "by", "params", "commit", "path", "tree", "budget", "to", "at")
            self.jobs[ev["id"]] = {**{k: ev[k] for k in asked}, "hours": ev.get("hours", HOURS), "status": "queued",
                                   "started": None, "ended": None, "outcome": None, "artifacts": None, "until": None,
                                   "tag": None}
        elif t == "claim":
            self.jobs[ev["id"]].update(status="running", started=ev["at"], until=ev["until"], tag=ev.get("tag"))
            self.tokens[ev["job_sha256"]] = ("job", ev["id"])
            self.tokens[ev["spend_sha256"]] = ("spend", ev["id"])
        elif t in ("finish", "cancel"):  # a canceled job stays canceled when its worker reports in
            job = self.jobs[ev["id"]]
            job["status"] = "canceled" if t == "cancel" or job["status"] == "canceled" else ev["status"]
            job["ended"] = job["ended"] or ev["at"]
            if t == "finish":
                job.update(outcome=ev["outcome"], artifacts=ev["artifacts"])
            # a canceled job's worker still reports, so its job token lasts until it has; its trials spend no more
            self.tokens = {h: (kind, j) for h, (kind, j) in self.tokens.items()
                           if j != ev["id"] or (t == "cancel" and kind == "job")}

    def view(self, job_id: str) -> dict:
        return dict(self.jobs[job_id])

    def auth(self, token: str):
        """("job" or "spend", the job) for a token minted at a claim, while it works, or None."""
        with self.team.lock:
            kind, job_id = self.tokens.get(sha(token), (None, None))
            job = self.jobs.get(job_id)
            return (kind, dict(job)) if job and time.time() < job["until"] else None

    def owner(self, req: dict):
        """Who asked for the job a request names, or None: what policy.authorize needs to know."""
        with self.team.lock:
            job = self.jobs.get(req.get("id")) if isinstance(req.get("id"), str) else None
            return job and job["by"]

    def find(self, req: dict) -> dict:
        job = self.jobs.get(string(req, "id", 20))
        if not job:
            raise NotFound(f"there is no job {req['id']}; see `swarmory job list`")
        return job

    def provenance(self, commit, path):
        """(the full commit id, the folder's path, its tree id) for a request's commit and path, checked against the
        run's repo."""
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{7,40}", commit):
            raise ValueError("commit is 7 to 40 hex characters: `swarmory job` pushes your HEAD and sends it for you")
        path = string({"path": path}, "path", 300).strip("/") or "."
        if path != "." and any(part in ("", ".", "..") for part in path.split("/")):
            raise ValueError("path is a folder's path in the repo, with no . or .. in it")
        full = git(self.repo, "rev-parse", "-q", "--verify", commit + "^{commit}", check=False)
        if not full:
            raise ValueError(f"{commit} is not a commit in this run's repo: push it first, as `swarmory job` does")
        full = full.decode().strip()
        tree = git(self.repo, "rev-parse", "-q", "--verify", f"{full}^{{tree}}" if path == "." else f"{full}:{path}",
                   check=False)
        tree = tree and tree.decode().strip()
        if not tree or git(self.repo, "cat-file", "-t", tree).strip() != b"tree":
            raise ValueError(f"{path} is not a folder in commit {full[:7]}")
        return full, path, tree

    def submit(self, who: str, req: dict) -> dict:
        """Queues a Harbor job on the folder `path` of `commit`, with Harbor's `params`, to run for at most `hours`.
        Its result is posted in `to`."""
        if not self.enabled:
            raise ValueError("this run has no jobs: start it with --jobs")
        nonce = string(req, "nonce", 64, False)
        params, (hours, budget) = req.get("params") or {}, limits(req)
        if not isinstance(params, dict):
            raise ValueError("params is an object")
        commit, path, tree = self.provenance(req.get("commit"), req.get("path"))
        params = harbor.check(params, Inputs(self.repo, tree), self.url)
        with self.team.lock:
            jid = self.team.seen(who, nonce, "job")
            if not jid:
                conv = self.team.resolve(who, req.get("to") or "general", write=True)
                jid = f"j{self.count + 1}"
                git(self.repo, "update-ref", f"refs/jobs/{jid}", commit)  # so no gc drops what the job ran on
                self.team.append({"type": "job", "id": jid, "by": who, "params": params, "commit": commit,
                                  "path": path, "tree": tree, "budget": budget, "hours": hours, "to": conv,
                                  "nonce": nonce})
                self.team.post(conv, who, f"queued job {jid}: {described(self.jobs[jid])}", None, None, quiet=True)
            return {"job": self.view(jid)}

    def list(self, who: str, req: dict) -> dict:
        """The run's jobs, newest first: with `mine` only the caller's, and with `tree`, or `commit` and `path`, only
        those on exactly that folder content."""
        tree = req.get("tree")
        if tree is None and req.get("path") is not None and req.get("commit") is None:
            raise ValueError("path needs the commit it is a folder of")
        if tree is None and req.get("commit") is not None:
            tree = self.provenance(req["commit"], req.get("path"))[2]
        if tree is not None and not (isinstance(tree, str) and re.fullmatch(r"[0-9a-f]{40}", tree)):
            raise ValueError("tree is a git tree id: `swarmory results PATH` works it out for you")
        with self.team.lock:
            jobs = [self.view(j) for j, job in reversed(self.jobs.items())
                    if (not req.get("mine") or job["by"] == who) and tree in (None, job["tree"])]
            return {"jobs": jobs[:int(number(req, "limit", 50, 1, 1000))]}

    def show(self, who: str, req: dict) -> dict:
        with self.team.lock:
            return {"job": self.view(self.find(req)["id"])}

    def cancel(self, who: str, req: dict) -> dict:
        with self.team.lock:
            job = self.find(req)
            if job["status"] not in ("queued", "running"):
                raise ValueError(f"{job['id']} is already {job['status']}")
            self.team.append({"type": "cancel", "id": job["id"], "by": who})
            self.team.post(job["to"], who, f"canceled job {job['id']}", None, None, quiet=True)
            return {"job": self.view(job["id"])}

    def workers(self) -> list:
        """(job id, when its worker started, or None) for each job whose worker has not reported: a claimed one, and
        a queued one, which starting its worker claims. A canceled job keeps its worker until it reports: Harbor runs
        under it until then."""
        with self.team.lock:
            return [(j, job["started"]) for j, job in self.jobs.items()
                    if job["status"] == "queued" or job["started"] and not job["outcome"]]

    def claim(self, jid: str):
        """The queued job `jid`, now running until its limit or the run's deadline, with the tag of its sandboxes, a
        new job token for its worker and a new spend token for its trials; None if it is not queued."""
        with self.team.lock:
            if self.jobs.get(jid, {}).get("status") != "queued":
                return None
            token, spend = secrets.token_hex(16), secrets.token_hex(16)
            self.team.append({"type": "claim", "id": jid, "job_sha256": sha(token), "spend_sha256": sha(spend),
                              "until": min(time.time() + self.jobs[jid]["hours"] * 3600, self.deadline),
                              "tag": secrets.token_hex(16)})
            return {"job": self.view(jid), "token": token, "spend": spend}

    def finish(self, who: str, req: dict) -> dict:
        """A claimed job ended: `done` or `failed`, with Harbor's `headline` and `summary`, an `error`, and its
        `artifacts` (a file). Its result is posted where it was asked for, to its submitter."""
        status, summary = req.get("status"), req.get("summary")
        outcome = {"headline": string(req, "headline", 300, False), "summary": summary,
                   "error": string(req, "error", 4000, False)}
        artifacts = string(req, "artifacts", 20, False)
        if status not in ("done", "failed") or summary is not None and not isinstance(summary, dict):
            raise ValueError("status is done or failed, and summary an object")
        with self.team.lock:
            job = self.find(req)
            if job["status"] not in ("running", "canceled") or not job["started"] or job["outcome"]:
                raise ValueError(f"{job['id']} is not a job a worker holds")
            if artifacts and artifacts not in self.team.files:
                raise ValueError(f"there is no file {artifacts}")
            self.team.append({"type": "finish", "id": job["id"], "status": status, "outcome": outcome,
                              "artifacts": artifacts})
            self.team.post(job["to"], SYSTEM, f"@{job['by']} job {job['id']} {verdict(job)}", None, None,
                           files=[artifacts] if artifacts else ())
            return {"job": self.view(job["id"])}
