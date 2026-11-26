"""The one loop that keeps every run's workers as the run wants them, through the server's backend.

Each tick reads what should run (Run.wanted: each agent of a running run that has a main, and a worker for each job it
holds or may claim) and what runs (the backend's `live`), and plan() says what to do: start, stop, or fail a job whose
worker is gone, which also ends the sandboxes its Harbor left. plan() is pure; tick() carries its steps out. An agent's
worker starts only once its clone, members/ID/work, exists: the server makes it from the run's repo. Before each start
the server writes the agent's starting prompt, rendered from main, to members/ID/prompt.md. Starting an agent mints its
token, so a restarted worker holds a new one and the old one stops working; starting a job's worker claims the job,
which mints its tokens."""
import json
import os
import secrets
import shutil
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from swarmory import harness
from swarmory.backends.base import WorkerSpec

from .jobs import SECRETS, git
from .policy import sha
from .prompt import mission, prompt
from .team import SYSTEM

TICK = 5  # seconds between ticks
RESTART = 60  # an agent's worker starts at most once a minute
GONE = 60  # seconds a job's worker may be missing from `live` after it started, before its job fails


@dataclass(frozen=True)
class Want:
    name: str             # RUN/agent/ID or RUN/job/JID
    run: bool             # it should be running now
    since: float | None   # when it last started, as the run's log has it, or None if it never has


@dataclass(frozen=True)
class Step:
    do: str    # "start", "stop", or "fail": end a job whose worker is gone
    name: str


def plan(wanted: list, live: frozenset, now: float, restart: float = RESTART) -> list:
    """What to do now. A live worker that should not run is stopped. A worker that should run and does not is started:
    an agent at most once every `restart` seconds, a job only once. A job whose worker started and is gone is failed,
    after GONE seconds while its run wants it and at once when its run has ended."""
    should = {w.name for w in wanted if w.run}
    steps = [Step("stop", n) for n in sorted(live - should)]
    for w in sorted(wanted, key=lambda w: w.name):
        if w.name in live:
            continue
        if w.since is None:
            steps += [Step("start", w.name)] if w.run else []
        elif w.name.split("/")[1] == "job":
            steps += [Step("fail", w.name)] if not w.run or now - w.since >= GONE else []
        elif w.run and now - w.since >= restart:
            steps.append(Step("start", w.name))
    return steps


def prepare(repo: Path, work: Path):
    """Makes the agent's clone `work` of the run's repo, with origin control-plane:work.git (its login's gitconfig
    points that at the server), unless it has one. A local clone: no network, and a clone cut short leaves only
    work.tmp, which the next try removes. A work folder with no .git is no clone, and the new one replaces it."""
    if (work / ".git").is_dir():
        return
    tmp = work.with_name("work.tmp")
    work.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
    git(repo, "clone", "--no-hardlinks", "-q", str(repo), str(tmp))
    git(tmp, "remote", "set-url", "origin", "control-plane:work.git")
    shutil.rmtree(work, ignore_errors=True)
    tmp.rename(work)


class Supervisor:
    def __init__(self, runs, backend, url: str, tick: float = TICK, restart: float = RESTART):
        self.runs, self.backend, self.url = runs, backend, url
        self.every, self.restart = tick, restart
        self.host = urlsplit(url).hostname
        self.lock = threading.Lock()  # one tick at a time: the loop's, or a verb's that asked for one now
        self.ticks = 0  # ticks done, so a tick knows whether another ran since it read what runs
        self.done = threading.Event()

    def loop(self):
        while not self.done.wait(self.every):
            try:
                self.tick()
            except Exception:  # a backend that did not answer this once: the next tick asks again
                traceback.print_exc()

    def tick(self, clone: bool = True):
        """One pass. Clones are made first, outside the lock, as a large repo takes a while: the stop verb, which ticks
        with clone=False, waits for no clone, so the loop's thread is the only one that makes them. What runs is read
        outside the lock too, as the backend may be slow to answer, and read again if another tick ran meanwhile: a
        plan on what ran before would start a running agent again, which revokes its token."""
        if self.done.is_set():
            return
        if clone:
            self.clone()
        while True:
            ran, live = self.ticks, self.backend.live()
            with self.lock:
                if self.done.is_set():  # the server is closing: nothing starts after close() returns
                    return
                if self.ticks != ran:
                    continue
                try:
                    now = time.time()
                    for run in self.runs.loaded():
                        try:
                            run.warn(now)
                        except Exception:
                            traceback.print_exc()
                    for step in plan([w for run in self.runs.loaded() for w in run.wanted(now)], live, now,
                                     self.restart):
                        try:
                            self.do(step)
                        except Exception:  # one worker's trouble never stops the rest
                            traceback.print_exc()
                finally:  # counted once its steps are done, so a tick that read `live` during them reads it again
                    self.ticks += 1
                return

    def home(self, run, wid: str) -> str:
        """An agent's folder in the server's data (WorkerSpec.home)."""
        return f"runs/{run.id}/members/{wid}"

    def work(self, run, wid: str) -> Path:
        """An agent's clone."""
        return self.runs.dir.parent / self.home(run, wid) / "work"

    def hosts(self, run) -> tuple:
        """The hosts an agent of `run` reaches on Modal: the server's, and the run's --allow."""
        return (self.host, *run.config.get("allow", ()))

    def clone(self):
        """Makes the clone of each agent that should run and has none."""
        now = time.time()
        for run in self.runs.loaded():
            for w in run.wanted(now):
                _, role, wid = w.name.split("/")
                if not w.run or role != "agent":
                    continue
                try:
                    prepare(run.repo, self.work(run, wid))
                except Exception:  # its worker waits for a clone, which the next tick tries again
                    traceback.print_exc()

    def do(self, step: Step):
        rid, role, wid = step.name.split("/")
        if step.do == "stop":
            return self.backend.stop(step.name)
        run = self.runs.get(rid)
        if step.do == "fail":
            run.jobs.finish(SYSTEM, {"id": wid, "status": "failed", "error": "Its worker stopped without reporting."})
            run.jobs.cleanup(run.jobs.view(wid))
        elif role == "agent":
            work = self.work(run, wid)
            if not (work / ".git").is_dir():  # a start would find no clone
                return
            brief, skills = mission(run.repo)
            (work.parent / "prompt.md").write_text(prompt(run.config, run.team.members[wid], brief, skills,
                                                          self.backend.kind, self.hosts(run), run.jobs.guide(),
                                                          run.lead() == wid))
            token = secrets.token_hex(16)
            run.team.worker(wid, sha(token))  # logged before the worker gets it
            self.backend.start(self.agent(run, run.team.members[wid], token))
        elif claimed := run.jobs.claim(wid):
            try:
                self.backend.start(self.job(run, claimed))
            except Exception as e:
                run.jobs.finish(SYSTEM, {"id": wid, "status": "failed", "error": f"Its worker did not start: {e}"})
                raise

    def agent(self, run, member: dict, token: str) -> WorkerSpec:
        c = run.config
        entry = run.proxy.litellm.find(member.get("model"))[1] or {}
        meta = {k: entry[k] for k in ("max_input_tokens", "max_output_tokens", "max_tokens", "supports_reasoning")
                if entry.get(k) is not None}
        return WorkerSpec(
            f"{run.id}/agent/{member['id']}",
            {"SWARMORY_URL": f"{self.url}/r/{run.id}", "SWARMORY_TOKEN": token, "SWARMORY_ID": member["id"],
             "SWARMORY_NAME": member["name"], "SWARMORY_HARNESS": member["harness"],
             "SWARMORY_MODEL": member.get("model") or "", "SWARMORY_DEADLINE": str(c["deadline"]),
             "SWARMORY_ARGS": "\n".join(member.get("args") or ()),
             **({"SWARMORY_MODEL_META": json.dumps(meta)} if meta else {}),
             **{f"SWARMORY_{k.upper()}": member[k] for k in harness.COMMAND_FIELDS if member.get(k)}},
            tuple(c.get("grants", ())), self.hosts(run), self.home(run, member["id"]))

    def job(self, run, claimed: dict) -> WorkerSpec:
        """A job's worker. It must be gone at SWARMORY_DEADLINE, the job's limit. HARBOR, when the server has one, is
        the command that runs Harbor in place of the pinned one. It has no host limit: Harbor reaches Modal's API."""
        job = claimed["job"]
        env = {"SWARMORY_URL": f"{self.url}/r/{run.id}", "SWARMORY_TOKEN": claimed["token"],
               "SWARMORY_SPEND": claimed["spend"], "SWARMORY_DEADLINE": str(job["until"]), "SWARMORY_JOB": job["id"]}
        if os.environ.get("HARBOR"):
            env["HARBOR"] = os.environ["HARBOR"]
        return WorkerSpec(f"{run.id}/job/{job['id']}", env, grants=SECRETS, home=f"runs/{run.id}/jobs/{job['id']}")
