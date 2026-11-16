"""The one server: it exports a run with no token or digest in it, a pasted token included, and `swarmory start` runs a
whole local run with one command. Agents are the fake harness's, or Stub's workers that run nowhere, and Harbor is a
stand-in that writes what Harbor writes."""
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

from conftest import OPENER, code, post, soon

from swarmory.control_plane.policy import sha

FAKE_LITELLM = Path(__file__).with_name("fake_litellm.py")
TASK = '[task]\nname = "t/hello"\n\n[environment]\nnetwork_mode = "no-network"\n'  # sealed, as a job needs it
FAKE_HARBOR = '''import json, pathlib, sys
args = sys.argv[1:]
arg = lambda flag: args[args.index(flag) + 1]
task, out, job = pathlib.Path(arg("-p")), pathlib.Path(arg("-o")), arg("--job-name")
for i in range(int(arg("-k"))):
    trial = out / job / f"{task.name}__{i}"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps({"verifier_result": {"rewards": {"reward": 1.0}}}))
'''


def pids(part: str) -> list:
    """The pids of the running processes whose command line holds `part`."""
    ps = subprocess.run(["ps", "-ww", "-eo", "pid=,stat=,args="], capture_output=True, text=True).stdout.splitlines()
    return [int(p) for p, stat, args in (line.split(None, 2) for line in ps) if part in args and stat[0] != "Z"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def mission(tmp: Path) -> Path:
    """A mission folder with one sealed Harbor task in it, tasks/hello."""
    folder = tmp / "hello"
    (folder / "tasks" / "hello").mkdir(parents=True)
    (folder / "BRIEF.md").write_text("# Say hello\n")
    (folder / "tasks" / "hello" / "task.toml").write_text(TASK)
    return folder


def test_an_export_holds_the_runs_records_and_no_token_or_digest(serve, tmp_path):
    served = serve()
    run = served.start(team=[{"harness": "claude-code"}] * 2, mission=mission(tmp_path), jobs=True)
    tokens = run.workers("ada", "grace")
    as_ada = {"Authorization": f"Bearer {tokens['ada']}"}
    run(tokens["ada"], "send", to="grace", text="the plan is in i1")
    remote = f"origin\thttp://swarmory:{tokens['ada']}@127.0.0.1:7700/r/{run.id}/git/work.git (fetch)"
    run(tokens["ada"], "send", to="general", text=f"my push fails, `git remote -v` says:\n{remote}")
    OPENER.open(urllib.request.Request(f"{run.url}/files?name=notes.txt", b"notes", as_ada)).read()
    head = subprocess.run(["git", "-C", str(served.server.data / "runs" / run.id / "work.git"), "rev-parse", "main"],
                          capture_output=True, text=True).stdout.strip()
    queued = run(tokens["ada"], "job", commit=head, path="tasks/hello", params={"agent": "oracle"})
    assert queued.get("job", {}).get("id") == "j1", queued
    assert soon(lambda: f"{run.id}/job/j1" in served.backend.specs)
    job = served.backend.specs[f"{run.id}/job/j1"].env
    said = f"my token is {tokens['ada']}, the link's {run.view}"
    for name, body in (("session-001.jsonl", json.dumps({"said": said}) + "\n"), ("agent.log", f"agent: {said}\n")):
        OPENER.open(urllib.request.Request(f"{run.url}/trace?name={name}&at=0", body.encode(), as_ada)).read()
    assert code(f"{run.url}/trace?name=agent.log&at=0", tokens["ada"], b"again") == 409, "the offset was not checked"

    feed = urllib.request.Request(f"{run.url}/feed?log=0&ledger=0", headers={"Authorization": f"Bearer {run.view}"})
    with OPENER.open(feed) as r:
        assert tokens["ada"].encode() not in r.read(), "the watch link's feed shows ada's run token"
    with OPENER.open(urllib.request.Request(f"{run.url}/export", headers={"Authorization": f"Bearer {served.token}"})) \
            as r:
        tar = tarfile.open(fileobj=io.BytesIO(r.read()))
    files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    held = ("config.json", "log.jsonl", "ledger.jsonl", "files/f1", "work.bundle",
            "members/ada/sessions/session-001.jsonl", "members/ada/agent.log")
    assert {f"{run.id}/{n}" for n in held} <= files.keys()
    log = files[f"{run.id}/log.jsonl"]
    assert b"the plan is in i1" in log and b"my push fails" in log and files[f"{run.id}/files/f1"] == b"notes"
    secrets = [served.token, run.view, *tokens.values(), job["SWARMORY_TOKEN"], job["SWARMORY_SPEND"]]
    for name, data in files.items():
        for secret in secrets:
            assert secret.encode() not in data and sha(secret).encode() not in data, (name, secret)
        if name.endswith((".json", ".jsonl")):
            assert not re.search(rb'"[a-z_]*sha256"', data), f"{name} holds a digest"
    for name in ("sessions/session-001.jsonl", "agent.log"):
        assert b"[hidden]" in files[f"{run.id}/members/ada/{name}"]
    assert post(run.url, tokens["ada"], "me")["me"]["id"] == "ada" and code(f"{run.url}/export", tokens["ada"]) == 403


def test_swarmory_start_runs_a_whole_local_run_with_one_command(fake_agents, tmp_path, monkeypatch):
    """`swarmory start`, with no server answering at the login, starts `swarmory serve` in the background and the run,
    and pushes the mission, which starts the agent. Then as the operator: a message wakes the agent, the agent's
    Harbor job runs in a local job worker and reports, `swarmory mcp` acts in the run, stop ends the workers, and
    `swarmory export` unpacks the run's records."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Vivek T\n")  # whom `swarmory start` makes the operator
    (tmp_path / "harbor.py").write_text(FAKE_HARBOR)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy")
    monkeypatch.setenv("SWARMORY_PROXY_LITELLM", str(FAKE_LITELLM))
    monkeypatch.setenv("HARBOR", f"{sys.executable} {tmp_path / 'harbor.py'}")
    port = free_port()
    url, token = f"http://127.0.0.1:{port}", "c0ffee" * 5 + "00"
    (tmp_path / "op-login").write_text(json.dumps({"url": url, "token": token}))

    def swarmory(*args, login=tmp_path / "op-login", cwd=tmp_path):
        p = subprocess.run([sys.executable, "-m", "swarmory.cli", *args], capture_output=True, text=True, cwd=cwd,
                           env={**os.environ, "SWARMORY_LOGIN": str(login)}, timeout=120)
        return p.stdout + p.stderr

    folder = mission(tmp_path)
    (folder / "team.toml").write_text('[[agent]]\nharness = "fake"\n')
    try:
        said = swarmory("start", str(folder), "--hours", "0.2", "--jobs")
        assert f"No server answers at {url}: starting one here" in said and "the agents are starting" in said, said
        rid = json.loads((tmp_path / "op-login").read_text())["run"]
        assert swarmory("use", rid) == f"`swarmory` now acts in {rid}.\n"
        ada = home / ".swarmory" / "data" / "runs" / rid / "members" / "ada"
        calls = ada / "home" / "calls.jsonl"
        assert soon(lambda: calls.is_file(), 30), "the seed push started no agent"
        assert swarmory("send", "ada", "wake up, ada") == "Sent m1 to dm:ada:vivek.\n"
        assert soon(lambda: "wake up, ada" in calls.read_text(), 30), "the message woke no one"

        queued = swarmory("job", "harbor", "tasks/hello", "--agent", "oracle",
                          login=ada / "home" / ".swarmory" / "login", cwd=ada / "work")
        assert queued.startswith("Queued j1: harbor on tasks/hello at "), queued
        done = "job j1 finished: 1 passed, 0 failed, 0 errored of 1"
        assert soon(lambda: done in swarmory("read", "--all"), 30), swarmory("read", "--all")
        assert re.search(r"ada +fake +default +running +1", swarmory("status")), swarmory("status")

        mcp = subprocess.Popen([sys.executable, "-m", "swarmory.cli", "mcp"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True, env={**os.environ, "SWARMORY_LOGIN": str(tmp_path /
                                                                                                         "op-login")})
        meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {}}
        answers = {}
        for n, name, args in ((1, "issues", {}), (2, "new", {"run": rid, "title": "From MCP"})):  # one at a time
            mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {
                "_meta": meta, "name": name, "arguments": args}}) + "\n")
            mcp.stdin.flush()
            answers[n] = json.loads(mcp.stdout.readline())["result"]
        mcp.stdin.close()
        assert answers[1]["structuredContent"] == {"issues": []} and not answers[2]["isError"], answers
        assert token not in json.dumps(answers), "an MCP answer held the operator token"

        assert swarmory("stop") == f"Stopped {rid}. It stays readable.\n"
        assert not list((home / ".swarmory" / "data" / "workers").glob("*.json")), "a worker outlived stop"
        assert f"{rid}" in swarmory("runs") and "stopped" in swarmory("runs")
        assert swarmory("export", rid, str(tmp_path / "out")).strip() == str(tmp_path / "out" / rid)
        log = (tmp_path / "out" / rid / "log.jsonl").read_text()
        assert "wake up, ada" in log and done in log and "From MCP" in log
    finally:  # the server, which stops its LiteLLM, then whatever workers it left, which outlive it by design
        [os.kill(pid, signal.SIGTERM) for pid in pids(f"swarmory.cli serve --port {port}")]
        for f in (home / ".swarmory" / "data" / "workers").glob("*.json"):
            try:
                os.killpg(json.loads(f.read_text())["pid"], signal.SIGKILL)
            except (OSError, ValueError):
                pass
