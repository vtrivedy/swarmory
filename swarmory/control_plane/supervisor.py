"""The one loop that keeps every run's workers as the run wants them, through the server's backend.

Each tick reads what should run (Run.wanted: each agent of a running run that has a main, and a worker for each job it
holds or may claim) and what runs (the backend's `live`), and plan() says what to do: start, stop, or fail a job whose
worker is gone, which also ends the sandboxes its Harbor left. plan() is pure; tick() carries its steps out. Starting
an agent mints its token, so a restarted worker holds a new one and the old one stops working; starting a job's worker
claims the job, which mints its tokens."""
import os
import secrets
import string
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from swarmory import harbor
from swarmory.backends.base import WorkerSpec

from .policy import sha
from .team import IDLE, SYSTEM

TICK = 5  # seconds between ticks
RESTART = 60  # an agent's worker starts at most once a minute
GONE = 60  # seconds a job's worker may be missing from `live` after it started, before its job fails
PROMPT = Path(__file__).resolve().parents[1] / "worker" / "prompt.md"


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


def prompt(config: dict, member: dict) -> str:
    """One agent's starting prompt (worker/prompt.md). $work, where its clone is, is left for the worker to fill in."""
    agents = [m for m in config["members"] if m["kind"] == "agent"]
    roster = [f"- {a['name']} ({a['id']}), {a['harness']}" + (f". Role: {a['role']}" if a.get("role") else "")
              for a in agents]
    roster.append(f"- {config['operator'].title()} ({config['operator']}), a person, runs this mission")
    return string.Template(PROMPT.read_text()).safe_substitute(
        name=member["name"], id=member["id"], n=len(agents), roster="\n".join(roster),
        role=f"Your role: {member['role']}\n\n" if member.get("role") else "",
        idle=f"{IDLE // 60}",
        deadline=time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(config["deadline"])))


class Supervisor:
    def __init__(self, runs, backend, url: str, tick: float = TICK, restart: float = RESTART):
        self.runs, self.backend, self.url = runs, backend, url
        self.every, self.restart = tick, restart
        self.host = urlsplit(url).hostname
        self.lock = threading.Lock()  # one tick at a time: the loop's, or a verb's that asked for one now
        self.done = threading.Event()

    def loop(self):
        while not self.done.wait(self.every):
            try:
                self.tick()
            except Exception:  # a backend that did not answer this once: the next tick asks again
                traceback.print_exc()

    def tick(self):
        with self.lock:
            if self.done.is_set():  # the server is closing: nothing starts after close() returns
                return
            now, live = time.time(), self.backend.live()
            for step in plan([w for run in self.runs.loaded() for w in run.wanted(now)], live, now, self.restart):
                try:
                    self.do(step)
                except Exception:  # one worker's trouble never stops the rest
                    traceback.print_exc()

    def do(self, step: Step):
        rid, role, wid = step.name.split("/")
        if step.do == "stop":
            return self.backend.stop(step.name)
        run = self.runs.get(rid)
        if step.do == "fail":
            run.jobs.finish(SYSTEM, {"id": wid, "status": "failed", "error": "Its worker stopped without reporting."})
            job = run.jobs.view(wid)
            if harbor.where(job["params"], self.url) == "modal":
                harbor.sweep(job["tag"])  # its Harbor died with it, and left its sandboxes running
        elif role == "agent":
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
        return WorkerSpec(
            f"{run.id}/agent/{member['id']}",
            {"SWARMORY_URL": f"{self.url}/r/{run.id}", "SWARMORY_TOKEN": token, "SWARMORY_ID": member["id"],
             "SWARMORY_NAME": member["name"], "SWARMORY_HARNESS": member["harness"],
             "SWARMORY_MODEL": member.get("model") or "", "SWARMORY_DEADLINE": str(c["deadline"]),
             "SWARMORY_PROMPT": prompt(c, member), "SWARMORY_ARGS": "\n".join(member.get("args") or ())},
            tuple(c.get("grants", ())), (self.host, *c.get("allow", ())), f"runs/{run.id}/members/{member['id']}")

    def job(self, run, claimed: dict) -> WorkerSpec:
        """A job's worker. It must be gone at SWARMORY_DEADLINE, the job's limit. HARBOR, when the server has one, is
        the command that runs Harbor in place of the pinned one. It has no host limit: Harbor reaches Modal's API."""
        job = claimed["job"]
        env = {"SWARMORY_URL": f"{self.url}/r/{run.id}", "SWARMORY_TOKEN": claimed["token"],
               "SWARMORY_SPEND": claimed["spend"], "SWARMORY_DEADLINE": str(job["until"]), "SWARMORY_JOB": job["id"]}
        if os.environ.get("HARBOR"):
            env["HARBOR"] = os.environ["HARBOR"]
        return WorkerSpec(f"{run.id}/job/{job['id']}", env, home=f"runs/{run.id}/jobs/{job['id']}")
