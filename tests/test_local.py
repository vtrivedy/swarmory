"""The local backend's workers, as processes on this machine: here a stand-in worker that sleeps, and a `ps` that
moves every start time it prints as a stepped wall clock does."""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from swarmory.backends import local
from swarmory.backends.base import WorkerSpec
from swarmory.control_plane.supervisor import RESTART, Supervisor, Want, plan

PS = '''import datetime, subprocess, sys
r = subprocess.run([{real!r}] + sys.argv[1:], capture_output=True, text=True)
shift = int(open({shift!r}).read())
for line in r.stdout.splitlines():
    stat, _, at = line.partition(" ")
    at = datetime.datetime.strptime(at.strip(), "%a %b %d %H:%M:%S %Y") + datetime.timedelta(seconds=shift)
    print(stat, at.strftime("%a %b %e %H:%M:%S %Y"))
sys.exit(r.returncode)
'''


@pytest.mark.skipif(sys.platform != "linux", reason="on Linux a worker's start time is not ps's")
def test_a_stepped_clock_starts_no_second_worker(tmp_path, monkeypatch):
    """A VM resumed from a pause steps its wall clock forward, which moves /proc/stat's btime, and with it the start
    time ps prints for every process. The worker still runs: the supervisor starts no other, and a stop ends it. A pid
    file swarmory 0.5.0 wrote, with ps's start time, still names its worker."""
    shift, ps, worker = tmp_path / "shift", tmp_path / "bin" / "ps", tmp_path / "worker"
    shift.write_text("0")
    ps.parent.mkdir()
    ps.write_text(f"#!{sys.executable}\n" + PS.format(real=shutil.which("ps"), shift=str(shift)))
    worker.write_text("#!/bin/sh\nexec sleep 600\n")
    for f in (ps, worker):
        f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{ps.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(sys, "executable", str(worker))  # Local.start runs `sys.executable -m swarmory.worker.agent`
    backend, name = local.Local(tmp_path / "data"), "R/agent/hopper"
    backend.start(WorkerSpec(name, {}, home="runs/R/members/hopper"))
    record = backend.recorded(name)
    pid = record["pid"]
    try:
        before = local.lstart(pid)
        backend.pidfile(name).write_text(json.dumps({"pid": pid, "started": before}))
        assert name in backend.live(), "a pid file from 0.5.0 lost its worker"
        backend.pidfile(name).write_text(json.dumps(record))

        shift.write_text("716")  # a 12-minute pause, then the clock stepped forward
        assert local.lstart(pid) != before, "the stand-in ps moved no start time"
        live = backend.live()
        assert name in live, "the running worker was taken for gone"
        assert backend.pidfile(name).exists()
        assert not plan([Want(name, True, 0)], live, RESTART + 1), "the supervisor would start a second worker"

        backend.stop(name)
        assert not Path(f"/proc/{pid}").exists(), "stop() left the worker running"
        assert not backend.live() and not backend.pidfile(name).exists()
    finally:
        try:
            os.killpg(pid, 9)
        except OSError:
            pass


def test_supervisor_reads_live_workers_outside_its_lock():
    seen = []

    class Stub:
        def live(self):
            seen.append(sup.lock.locked())
            return frozenset()

    runs = type("Runs", (), {"loaded": lambda self: []})()
    sup = Supervisor(runs, Stub(), "http://127.0.0.1:7700")
    sup.tick()
    assert seen == [False]
