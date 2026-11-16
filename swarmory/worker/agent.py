"""An agent's life: clone the repo, then until the deadline run a session of its coding agent, sleep in `swarmory sleep`
until something needs it, and resume. The server's supervisor starts it through a backend and sets its SWARMORY_*
environment: SWARMORY_URL, the token it minted for this start, and the agent's settings. What it knows about the coding
agent comes from that harness's file in swarmory/harness/. Standard library only: it runs on the agent's machine.
An agent no one wakes is woken anyway SWARMORY_WAKE_EVERY seconds after its last session ends, to find work.

Its HOME is a folder of SWARMORY_DIR, which outlives its machine, so a restart finds the coding agent's conversations
where it left them. Everything it starts runs in its own process group, which it leads. The deadline stops a session,
and at its end, by the deadline or a stop, the worker sends its sessions' last lines and then stops the whole group."""
import contextlib
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from swarmory import POST_BUFFER, WAKE_EVERY, harness
from swarmory.harness.base import Endpoint
from swarmory.worker import LAST

TICK = 5  # seconds between trace uploads, and between tries at the first clone
SYNC = 60  # seconds between syncs of SWARMORY_DIR: a lost machine loses at most this much of the running session
RETRY = 60  # seconds before a failed session or a failed `swarmory sleep` is tried again
GRACE = 30  # seconds a command gets to stop at the deadline before it is killed
LATE = 60  # seconds before the deadline after which no periodic wake starts a session
IDLE = "No one has woken you for a few minutes"  # how a periodic wake's prompt starts
ENV = os.environ
DEADLINE = int(ENV.get("SWARMORY_DEADLINE") or 0)


def say(text: str):
    print(text, flush=True)


def left() -> float:
    return DEADLINE - time.time()


def nap(seconds: float):
    time.sleep(max(0, min(seconds, left())))


def periodic() -> str:
    """The prompt of a wake that no one sent: the agent slept `every` seconds and the run still has time."""
    hours, minutes = divmod(int(left() // 60), 60)
    return (f"{IDLE}, and the run has {f'{hours} h ' if hours else ''}{minutes} min "
            "left. Run `swarmory` and `git log` to see the board. Then pick or make work: claim an open item, open a "
            "new idea, repair a draft upward, or add seeds or trials to a candidate. Or review, break or debate a "
            "teammate's work. Tell its owner only what they need to act on, never with @all, and don't post that you "
            "are idle or what you plan. End your turn only after you have started something real: a commit, a job, "
            "a posted review or a claimed item. If a Harbor job you asked for is running (`swarmory job list --mine`), "
            "check it; its result wakes you.")


class Agent:
    def __init__(self):
        self.harness = harness.named(ENV["SWARMORY_HARNESS"])
        self.dir = Path(ENV["SWARMORY_DIR"])  # its folder in the run, which outlives a restart and holds its HOME
        self.work = str(self.dir / "work")
        self.prompt = ENV["SWARMORY_PROMPT"].replace("$work", self.work)  # the server can't know where the clone is
        self.sessions = self.dir / "sessions"
        self.conversations = Path.home() / self.harness.home / self.harness.conversations
        self.model = ENV.get("SWARMORY_MODEL") or None
        self.args = [a for a in ENV.get("SWARMORY_ARGS", "").split("\n") if a]  # team.toml's args, one per line
        self.every = float(ENV.get("SWARMORY_WAKE_EVERY") or WAKE_EVERY)  # the run's wake_every; 0 is never
        self.running = None  # the command run() waits on
        self.lock = threading.Lock()  # the saver and a session's end both upload

    def run(self, cmd: list, **popen) -> tuple[int, str]:
        """Runs cmd: (its exit status as a shell gives it, its stdout if `stdout` is a pipe). The deadline stops it,
        so nothing hangs past it on the server."""
        p = self.running = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, **popen)
        try:
            out, _ = p.communicate(timeout=max(0, left()))
        except subprocess.TimeoutExpired:
            p.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(GRACE)
            p.kill()
            out, _ = p.communicate()
        self.running = None
        return (p.returncode if p.returncode >= 0 else 128 - p.returncode), out or ""

    def started(self) -> bool:
        """The harness has a conversation to resume."""
        return any(self.conversations.rglob("*.jsonl"))

    def count(self) -> int:
        return len(list(self.sessions.glob("session-*.jsonl")))

    def upload(self, sync: bool):
        """Sends the server the sessions' new lines, and with `sync` has the machine keep SWARMORY_DIR (on Modal,
        `sync` commits the Volume: a restart resumes from here)."""
        with self.lock:
            subprocess.run(["swarmory", "trace", str(self.sessions)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if sync:
                subprocess.run(["sync", str(self.dir)])

    def saver(self):
        since = 0
        while left() > 0:
            time.sleep(TICK)
            since += TICK
            self.upload(sync=since >= SYNC)
            since %= SYNC

    def mark(self, file: Path, event: str, **more):
        """The line before and after a session's own output that gives its trace the prompt it got and how it ended."""
        with open(file, "a") as f:
            f.write(json.dumps({"type": "swarmory", "event": event, "at": time.time(), "harness": self.harness.name,
                                "model": self.model, **more}) + "\n")

    def clone(self):
        """The agent's clone, made once. Runs the mission's setup.sh in it before the first session."""
        while not Path(self.work, ".git").is_dir() and self.run(
                ["git", "clone", "-q", "control-plane:work.git", self.work])[0]:
            if left() <= 0:
                sys.exit(0)
            say("waiting for the git remote")
            nap(TICK)
        os.chdir(self.work)
        Path(".git/index.lock").unlink(missing_ok=True)  # a git command the restart cut short; nothing else runs yet
        if Path("setup.sh").is_file() and self.count() == 0:
            say("running setup.sh")
            status, _ = self.run(["bash", "setup.sh"])
            if status:
                say(f"setup.sh failed ({status})")

    def session(self, prompt: str, fresh: str) -> int:
        """One coding-agent session, its transcript kept. Returns its exit status. `prompt` is for the conversation it
        resumes. With none to resume it gets the starting prompt and `fresh`, what set_aside says of the one before."""
        n = f"{self.count() + 1:03d}"  # after a restart, numbering goes on
        llm, resumed = Endpoint(ENV["SWARMORY_URL"].rstrip("/") + "/llm", ENV["SWARMORY_TOKEN"]), self.started()
        if not resumed:
            prompt = self.prompt + (f"\n\n{fresh}" if fresh else "")
        self.harness.prepare(self.work, llm)
        cmd = self.harness.command(prompt, resumed, self.model, self.args)
        say(f"session {n} started ({'resumed' if resumed else 'new'})")
        file, err = self.sessions / f"session-{n}.jsonl", self.sessions / f"session-{n}.err"
        file.write_bytes(b"")
        self.mark(file, "start", prompt=prompt, resumed=resumed)
        with open(file, "ab") as out, open(err, "wb") as errors:
            # not the worker's PYTHONPATH, which is for its own imports: the session's `swarmory` sets its own
            env = {k: v for k, v in {**ENV, **self.harness.env(llm)}.items() if k != "PYTHONPATH"}
            status, _ = self.run(cmd, env=env, stdout=out, stderr=errors)
        with open(err, "rb") as f:
            f.seek(max(0, err.stat().st_size - 2000))
            self.mark(file, "end", status=status, stderr=f.read().decode(errors="replace") if status else "")
        say(f"session {n} ended with {status}")
        self.upload(sync=True)
        return status

    def set_aside(self, fails: int, last: int) -> str:
        """A conversation that keeps failing (a provider refusal sticks to it) is moved out of the harness's home, so
        the next session starts a fresh one. Returns what that one is told."""
        try:
            self.conversations.rename(self.dir / f"{self.conversations.name}.failed-{int(time.time())}")
            say(f"starting a fresh conversation after {fails} failed sessions")
        except OSError:
            pass
        return (f"Your earlier conversation failed {fails} times in a row (exit {last}), so this is a fresh one. "
                "Your clone,\nthe board and the chat are as you left them: run `swarmory` and `git log` to see where "
                "things stand.")

    def wake(self, every: float = None) -> str:
        """Sleeps in `swarmory sleep` until something needs the agent: the wake line, the next session's prompt. With
        no wake for `every` seconds (the run's) from now, the end of the last session, it wakes the agent anyway with
        periodic(), unless the deadline is under LATE seconds off by then. That wake waits for a sleep that reached
        the server: while the server is down a session could only fail. Empty at the deadline."""
        every = self.every if every is None else every
        later = lambda t: t if DEADLINE - t >= LATE else math.inf  # noqa: E731
        due, reached = later(time.time() + every) if every else math.inf, False
        while left() > 0:
            if time.time() >= due:
                if reached:
                    return periodic()
                due = later(time.time() + RETRY)  # once a sleep reaches the server again
            code, woke = self.run(["swarmory", "sleep", "--timeout", str(math.ceil(min(left(), due - time.time())))],
                                  stdout=subprocess.PIPE, text=True)
            if code == 0 and woke.strip():
                return woke.rstrip("\n")
            reached = code in (0, 4)  # 4 is a timeout
            if not reached and left() > 0:
                say(f"swarmory sleep failed ({code}), retrying in {RETRY}s")
                nap(min(RETRY, max(0, due - time.time())))
        return ""

    def spent(self) -> bool:
        """The run's budget is spent, so every session fails until the operator raises it."""
        code, out = self.run(["swarmory", "spend", "--json"], stdout=subprocess.PIPE, text=True)
        try:
            r = json.loads(out) if code == 0 else {}
        except ValueError:
            return False
        budget = r.get("budget")
        return budget is not None and sum(u.get("usd") or 0 for u in r.get("by", {}).values()) >= budget

    def live(self):
        self.sessions.mkdir(parents=True, exist_ok=True)
        login = Path.home() / ".swarmory" / "login"  # each start's token is new, and replaces the last one's
        subprocess.run(["swarmory", "login", ENV["SWARMORY_URL"], ENV["SWARMORY_TOKEN"]], stdout=subprocess.DEVNULL)
        for key, value in (("user.name", ENV["SWARMORY_NAME"]), ("user.email", f"{ENV['SWARMORY_ID']}@swarmory.local"),
                           # one request per push: the server takes no chunked bodies
                           ("http.postBuffer", str(POST_BUFFER)), ("pull.rebase", "true"),
                           ("include.path", str(login.parent / "gitconfig"))):
            subprocess.run(["git", "config", "--global", key, value])
        self.clone()
        threading.Thread(target=self.saver, daemon=True).start()
        prompt = fresh = ""  # what the next session's conversation is told: one it resumes, or a fresh one
        if self.started():
            prompt = ("Your machine restarted. Your conversation and your clone are as you left them. "
                      + self.run(["swarmory"], stdout=subprocess.PIPE, text=True)[1].rstrip("\n"))
        news, fails = "", 0  # the wake a session started on, repeated to its retries; failed sessions in a row
        while left() > 0:
            last, fresh = self.session(prompt, fresh), ""
            if left() <= 0:
                break
            if last and self.spent():  # no call can succeed: sleep until a message, and keep the conversation
                say("the run's budget is spent: sleeping until a message wakes this agent")
                fails = 0
                prompt = news = self.wake(every=0)
                continue
            if last:  # a failed session is retried a minute later, until the deadline
                fails += 1
                say(f"retrying in {RETRY}s")
                nap(RETRY)
                news = periodic() if news.startswith(IDLE) else news  # its time left, now
                prompt = f"Your last session ended with exit {last}." + (f" {news}" if news else "")
                if fails >= 2:
                    fresh = self.set_aside(fails, last)
                    fails = 0
                continue
            fails = 0
            prompt = news = self.wake()
        say(f"Deadline reached after {self.count()} sessions.")


def main():
    def stop(sig, _):
        raise SystemExit(128 + sig)

    with contextlib.suppress(OSError):  # it leads a process group of its own, as a local backend's worker already does
        os.setpgid(0, 0)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    agent = Agent()
    try:
        agent.live()
    finally:  # at the deadline, or told to stop: its last lines are sent, and then nothing it started outlives it
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        agent.upload(sync=True)
        os.killpg(0, signal.SIGTERM)
        if agent.running:
            with contextlib.suppress(subprocess.TimeoutExpired):
                agent.running.wait(LAST)
        os.killpg(0, signal.SIGKILL)  # this process too: it is done


if __name__ == "__main__":
    main()
