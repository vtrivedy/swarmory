"""The job worker: runs Harbor for one job, then exits. The server's supervisor claims each job and starts this for
it through a backend.

    python3 -m swarmory.worker.job

It reads from its environment:
    SWARMORY_URL, SWARMORY_TOKEN   the run on the server, and the job's token: it opens this job, its input and its
                                   finish
    SWARMORY_SPEND                 the job's spend token, which its trials call models with through the server's
                                   gateway
    SWARMORY_JOB                   the job's id

The worker fetches the job's input from the server (a `git archive` of the tree the job recorded), runs Harbor
on it with only the environment swarmory/harbor.py lists, which holds neither the job's token nor anything else
of the worker's, sends what Harbor wrote as the job's artifacts, and finishes the job. Harbor runs in the worker's
process group, so a backend that stops the worker stops Harbor with it. A cancel or the job's time limit ends Harbor.
The worker then ends any sandbox Harbor left on Modal, and reports what it has."""
import contextlib
import http.client
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from swarmory import harbor
from swarmory.worker import LAST

POLL = 5  # seconds between looks at whether the job was canceled
REPORT = 300  # seconds before the job's limit, when its token ends, that Harbor is stopped: time to send what it has
PATIENCE = 60  # seconds Harbor gets to stop its trials on SIGTERM, before SIGKILL
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Server:
    def __init__(self, url: str, token: str):
        self.url, self.token = url, token

    def request(self, path: str, body: bytes = None, headers=None, timeout: int = 60) -> bytes:
        """A request with the job's token, retried for 5 minutes while the server doesn't answer (a restarting
        server)."""
        for delay in (5, 10, 20, 30, 60, 60, 60, 60, None):
            req = urllib.request.Request(self.url + path, body, {"Authorization": "Bearer " + self.token,
                                                                 **(headers or {})})
            try:
                with OPENER.open(req, timeout=timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code < 500 or delay is None:
                    raise RuntimeError(f"the server answered {e.code} to {path.split('?')[0]}: "
                                       f"{e.read()[:300]!r}") from None
            except (OSError, http.client.HTTPException) as e:
                if delay is None:
                    raise RuntimeError(f"the server at {self.url} does not answer: {e}") from None
            time.sleep(delay)

    def api(self, verb: str, body: dict) -> dict:
        return json.loads(self.request("/api/" + verb, json.dumps(body).encode(),
                                       {"Content-Type": "application/json"}))["data"]

    def canceled(self, job_id: str) -> bool:
        try:
            return self.api("job-show", {"id": job_id})["job"]["status"] == "canceled"
        except Exception:  # can't tell now: ask again next time
            return False


def run(server: Server, job_id: str, spend: str, work: Path) -> dict:
    """Runs the job in `work`; what finish() sends the server, with the artifacts' folder as `out`."""
    job = server.api("job-show", {"id": job_id})["job"]
    task, out = work / "input" / (Path(job["path"]).name if job["path"] != "." else "repo"), work / "out"
    task.mkdir(parents=True)
    out.mkdir()
    with tarfile.open(fileobj=io.BytesIO(server.request(f"/job/input?id={job_id}", timeout=600))) as tar:
        tar.extractall(task, filter="data")  # no links out, no absolute paths, no devices
    argv, env = harbor.command(job, task, out, server.url + "/llm", spend)
    log, stop = out / "harbor.log", None
    end = job["until"] - min(REPORT, (job["until"] - time.time()) / 4)  # time to report in, of a short job too
    with open(log, "wb") as f:
        p = subprocess.Popen(argv, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT)
        while p.poll() is None and stop is None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(POLL)
            if p.poll() is None and (time.time() > end or server.canceled(job_id)):
                stop = "it ran past its time" if time.time() > end else "it was canceled"
        if stop:
            p.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(PATIENCE)
            p.kill()
            p.wait()
    result = harbor.report(out / job_id, p.returncode, log)
    try:
        if harbor.where(job["params"], server.url) == "modal":
            harbor.sweep(job["tag"])
    except Exception as e:
        print(f"worker: could not end the sandboxes Harbor left: {e}", file=sys.stderr)
    if stop:  # with the trials that finished first
        result.update(status="failed", error=f"The job stopped because {stop}. Harbor's output ends:\n"
                      + log.read_text(errors="replace")[-3000:])
    return {**result, "out": out}


def main():
    server, job_id = Server(os.environ["SWARMORY_URL"], os.environ["SWARMORY_TOKEN"]), os.environ["SWARMORY_JOB"]
    with tempfile.TemporaryDirectory(prefix=f"swarmory-{job_id}-") as tmp:
        try:
            result = run(server, job_id, os.environ["SWARMORY_SPEND"], Path(tmp))
        except Exception as e:
            result = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
        if result.get("error"):
            result["error"] = result["error"][-4000:]  # the server's most
        out = result.pop("out", None)
        if out and any(out.iterdir()):
            archive = Path(tmp) / f"job-{job_id}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(out, arcname=f"job-{job_id}")
            try:
                sent = server.request(f"/files?name={archive.name}", archive.read_bytes(),
                                  {"Content-Type": "application/octet-stream"}, 600)
                result["artifacts"] = json.loads(sent)["file"]["id"]
            except RuntimeError as e:
                print(f"worker: the job's artifacts could not be sent: {e}", file=sys.stderr)
    print(f"worker: {job_id} {result['status']}", flush=True)
    server.api("finish", {"id": job_id, **result})
    # It leads its process group when a backend started it, and the group is then only what it started: whatever
    # Harbor left running goes too. Started by a shell or a test it is in their group, which is not its to stop.
    if os.getpgrp() == os.getpid():
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        os.killpg(0, signal.SIGTERM)
        time.sleep(LAST)
        os.killpg(0, signal.SIGKILL)  # this worker too, its work done


if __name__ == "__main__":
    main()
