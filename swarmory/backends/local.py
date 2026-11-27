"""Workers as process groups on the server's machine. They run as the operator, with the coding agents' permission
prompts off, and reach any host. An agent's home is its folder in the run, DATA/runs/RUN/members/ID; a job's worker
keeps the server's HOME, where Harbor finds the operator's Modal login.

Each worker has a pid file in DATA/workers that records its pid and when it started. A worker is live while a process
of that pid has that start time, so a restarted server finds the workers the last one started, and a pid reused by
another process is not mistaken for one."""
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


from .base import WorkerSpec

KEEP = ("PATH", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "SHELL", "TMPDIR", "PYTHONPATH")  # of the server's
STOP = 10  # seconds a worker's process group gets to go on SIGTERM, before SIGKILL
CODE = str(Path(__file__).resolve().parents[2])  # the folder that holds this server's swarmory, for workers to import


def tools() -> str:
    """Where workers find a coding agent's CLI: ~/.local/bin, then the server's PATH."""
    return f"{Path.home()}/.local/bin:{os.environ.get('PATH', '/usr/bin:/bin')}"


def started(pid: int) -> str:
    """When pid started, or "" if it does not run (a zombie does not). With the pid it names one process: a recorded
    pid may since belong to another. Not its command line, which macOS's Python changes by running itself again. On
    Linux it is the kernel's clock ticks from boot to the start, which nothing changes. Not ps's lstart there: ps adds
    those ticks to /proc/stat's btime, which moves when the wall clock is stepped (a VM resumed from a pause, NTP), and
    then every worker would stop matching its pid file at once and be started again beside itself."""
    if sys.platform != "linux":
        return lstart(pid)
    try:  # the fields after "(comm)", which may hold spaces or parentheses: state is field 3, starttime field 22
        state, *fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
    except OSError:
        return ""
    return "" if state == "Z" else f"ticks {fields[18]}"


def lstart(pid: int) -> str:
    """When pid started, as ps prints it, or "" if it does not run."""
    r = subprocess.run(["ps", "-o", "stat=,lstart=", "-p", str(pid)], capture_output=True, text=True)
    stat, _, at = r.stdout.strip().partition(" ")
    return "" if r.returncode or stat.startswith("Z") else at.strip()


def running(r) -> bool:
    """The worker a pid file records still runs. A pid file swarmory 0.5.0 wrote has ps's lstart, even on Linux."""
    at = r and r.get("started")
    return bool(at) and at == (started if at.startswith("ticks ") else lstart)(r["pid"])


class Local:
    kind = "local"

    def __init__(self, data: Path):
        self.data, self.pids = Path(data), Path(data) / "workers"
        self.children = {}  # name: the Popen of a worker this server started, polled so it leaves no zombie
        self.pids.mkdir(parents=True, exist_ok=True)
        swarm = self.data / "bin" / "swarmory"  # for workers: this server's own swarmory, whatever is installed
        swarm.parent.mkdir(exist_ok=True)
        swarm.write_text(f'#!/bin/sh\nPYTHONPATH={shlex.quote(CODE)}${{PYTHONPATH:+:$PYTHONPATH}} exec '
                         f'{shlex.quote(sys.executable)} -m swarmory.cli "$@"\n')
        swarm.chmod(0o755)

    def pidfile(self, name: str) -> Path:
        return self.pids / (name.replace("/", "+") + ".json")

    def start(self, spec: WorkerSpec):
        if spec.name in self.live():
            return
        home = self.data / spec.home
        (home / "home" if spec.role == "agent" else home).mkdir(parents=True, exist_ok=True)
        env = {k: os.environ[k] for k in KEEP if k in os.environ}
        if spec.role == "agent":  # its HOME is its own, with what it installs there on its PATH, and it gets its grants
            names = spec.grants
            env.update(PATH=f"{self.data / 'bin'}:{home / 'home' / '.local' / 'bin'}:{tools()}", SWARMORY_DIR=str(home),
                       HOME=str(home / "home"),
                       # the person's uv cache, so uv downloads each package once for every agent
                       UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv"))
        else:  # a job's worker: the server's HOME, and the variables its spec names, if the server's environment has them
            names = spec.grants
            env.update(PATH=f"{self.data / 'bin'}:{tools()}", HOME=os.environ.get("HOME", str(home)))
        env.update(GIT_CONFIG_NOSYSTEM="1",  # skips e.g. macOS git's osxkeychain helper, which hangs a headless clone
                   **spec.env, **{n: os.environ[n] for n in names if n in os.environ})
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (CODE, env.get("PYTHONPATH"))))  # this server's swarmory
        with open(home / f"{spec.role}.log", "ab") as log:  # its own process group, so a stop reaches all it started
            p = subprocess.Popen([sys.executable, "-m", f"swarmory.worker.{spec.role}"], env=env, cwd=home,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.children[spec.name] = p
        tmp = self.pidfile(spec.name).with_suffix(".tmp")
        tmp.write_text(json.dumps({"pid": p.pid, "started": started(p.pid)}))
        tmp.replace(self.pidfile(spec.name))

    def recorded(self, name: str):
        try:
            return json.loads(self.pidfile(name).read_text())
        except (OSError, ValueError):
            return None

    def live(self) -> frozenset:
        """The workers whose recorded process still runs. A worker that has ended loses its pid file."""
        for p in list(self.children.values()):
            p.poll()
        names = set()
        for f in self.pids.glob("*.json"):
            r = self.recorded(name := f.stem.replace("+", "/"))
            if running(r):
                names.add(name)
            else:
                f.unlink(missing_ok=True)
                self.children.pop(name, None)
        return frozenset(names)

    def stop(self, name: str):
        r, child = self.recorded(name), self.children.pop(name, None)
        if running(r):
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(r["pid"], sig)
                except OSError:
                    break
                end = time.monotonic() + STOP
                while sig == signal.SIGTERM and time.monotonic() < end:
                    if child:
                        child.poll()  # a zombie leader would keep its group alive
                    try:
                        os.killpg(r["pid"], 0)
                    except OSError:
                        break
                    time.sleep(0.1)
        if child:
            child.poll()
        if r and self.recorded(name) == r:  # not a newer worker's record, written meanwhile
            self.pidfile(name).unlink(missing_ok=True)

    def image(self, script: str) -> str:
        return ""  # agents run on this machine, with its tools

    def check(self, harnesses: list, grants: list, allow: list):
        for h in harnesses:
            if not shutil.which(h.cli, path=tools()):
                raise ValueError(f"The team has {h.name} agents, but there is no `{h.cli}` on the server's PATH or in "
                                 "~/.local/bin. Install it.")
        for g in grants:
            if not re.fullmatch(r"[A-Za-z_]\w*", g) or not os.environ.get(g):
                raise ValueError(f"--grant {g}: the server's environment has no {g} to give the agents.")
        if allow:
            raise ValueError(f"--allow {allow[0]}: local agents reach any host, so --allow is for a server on Modal.")
