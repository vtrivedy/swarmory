"""The one server: it exports a run with no token or digest in it, a pasted token included, and `swarmory start` runs a
whole local run with one command. Agents are the fake harness's, or Stub's workers that run nowhere, and Harbor is a
stand-in that writes what Harbor writes."""
import dataclasses
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import OPENER, code, post, soon

from swarmory import cli
from swarmory.control_plane import verbs
from swarmory.control_plane.policy import sha
from swarmory.control_plane.prompt import mission as read_mission
from swarmory.control_plane.prompt import prompt
from swarmory.control_plane.supervisor import prepare

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


def test_an_agents_clone_is_made_before_its_worker_starts_and_only_once(serve, tmp_path):
    """The server clones the run's repo into members/ID/work, with origin control-plane:work.git, writes the starting
    prompt beside it, before the worker starts. A restart finds the clone and clones nothing; a clone cut short leaves
    work.tmp, which the next removes, and a work folder with no .git is replaced."""
    served = serve()
    data, backend = Path(served.server.data), served.backend
    seen, start = [], backend.start
    backend.start = lambda spec: (seen.append(((data / spec.home / "work" / ".git").is_dir(),
                                               (data / spec.home / "prompt.md").is_file())),
                                  start(spec))
    run = served.start()
    run.workers("ada")
    work = data / "runs" / run.id / "members" / "ada" / "work"
    assert seen == [(True, True)], "the worker started before its clone and prompt were made"
    origin = subprocess.run(["git", "-C", str(work), "remote", "get-url", "origin"], capture_output=True, text=True)
    assert origin.stdout.strip() == "control-plane:work.git"
    assert (work / "BRIEF.md").is_file()

    (work / "mine").write_text("an agent's work")
    backend.up.clear()  # its worker is gone
    served.server.supervisor.clone()
    assert (work / "mine").is_file(), "a restart cloned again"

    served.server.supervisor.done.set()  # its loop would clone into the same folder as this test's prepare()
    shutil.rmtree(work / ".git")  # a work folder that is no clone
    (work.with_name("work.tmp") / "half").mkdir(parents=True)
    prepare(Path(served.server.runs.get(run.id).repo), work)
    assert not (work / "mine").exists() and (work / "BRIEF.md").is_file() and not work.with_name("work.tmp").exists()


def test_the_starting_prompt_holds_the_brief_the_skills_and_the_agents_machine(serve, tmp_path):
    """The prompt has BRIEF.md whole, each skill on main with its description (one over several lines too), and says
    what the agent's machine reaches and whether the run has Harbor jobs, and where their trials run."""
    folder = mission(tmp_path)
    (folder / "BRIEF.md").write_text("# Say hello\n\nCost: $5 at most. ${role} is not a variable.\n")
    for name, front in (("greeter", "description: Greets people. Use when\n  someone arrives.\n"),
                        ("quoted", 'name: quoted-skill\ndescription: "Says: hi"\n')):
        (folder / ".agents" / "skills" / name).mkdir(parents=True)
        (folder / ".agents" / "skills" / name / "SKILL.md").write_text(f"---\n{front}---\n\n# {name}\n")
    (folder / ".agents" / "skills" / "notes.md").write_text("not a skill\n")
    served = serve()
    run = served.start(team=[{"harness": "claude-code", "role": "Greet people."}], mission=folder, jobs=True)
    run.workers("ada")
    text = (Path(served.server.data) / "runs" / run.id / "members" / "ada" / "prompt.md").read_text()
    assert "<brief>\n# Say hello\n\nCost: $5 at most. ${role} is not a variable.\n</brief>" in text
    assert ("- greeter (.agents/skills/greeter/SKILL.md): Greets people. Use when someone arrives.\n"
            "- quoted-skill (.agents/skills/quoted/SKILL.md): Says: hi\n") in text
    assert "Your role: Greet people." in text and "The network is open." in text
    assert "in Docker on the server's machine" in text and "$" not in text.replace("$5", "").replace("${role}", "")

    r = served.server.runs.get(run.id)
    brief, skills = read_mission(r.repo)
    member = r.team.members["ada"]
    modal = prompt({**r.config, "jobs": False}, member, brief, [], "modal", ("x.modal.run", "pypi.org"), "")
    assert "It reaches only these hosts: x.modal.run, pypi.org." in modal and "The network is open." not in modal
    assert "has no jobs" in modal and "swarmory job harbor" not in modal and "mission's skills" not in modal
    r.jobs.url = "https://x.modal.run"  # a deployed server: trials run on Modal
    assert "on Modal" in prompt(r.config, member, brief, skills, "modal", (), r.jobs.guide())


def test_a_dry_start_checks_the_run_and_starts_nothing(serve, monkeypatch):
    served = serve()
    team = [{"harness": "claude-code"}, {"harness": "codex", "model": "gpt-6.1-sol", "name": "lead"}]
    budgeted = served("start", mission="hello", team=team, me="vivek", budget=5, dry=True)
    assert budgeted["status"] == 400 and "give the claude-code agent a model" in budgeted["error"], budgeted
    dry = served("start", mission="hello", team=team, me="vivek", budget=None, dry=True)
    assert dry.pop("deadline") > time.time() + 3 * 3600 - 60
    assert dry == {"dry": True, "server": served.url, "budget": None, "me": "vivek",
                   "members": [{"id": "ada", "harness": "claude-code", "model": None},
                               {"id": "lead", "harness": "codex", "model": "gpt-6.1-sol"}]}, dry
    assert served("runs") == {"runs": []}
    monkeypatch.delenv("OPENAI_API_KEY")
    refused = served("start", mission="hello", team=team, me="vivek", dry=True)
    assert refused["status"] == 400 and "needs OPENAI_API_KEY" in refused["error"], refused


def test_a_budgeted_run_refuses_a_model_the_server_cannot_price():
    from swarmory.control_plane.runs import new_run
    backend = type("B", (), {"check": lambda self, *a: None})()
    env = {"OPENAI_API_KEY": "k"}
    req = {"mission": "m", "team": [{"harness": "codex", "model": "gpt-new"}], "me": "vivek"}
    owner = lambda m: "openai"  # noqa: E731
    with pytest.raises(ValueError, match="no price for 'gpt-new'"):
        new_run({**req, "budget": 5}, backend, env, "t", owner, priced=lambda m: m != "gpt-new")
    assert new_run({**req, "budget": None}, backend, env, "t", owner, priced=lambda m: False)[0]["budget"] is None


def test_a_subscription_run_refuses_another_providers_model():
    from swarmory.control_plane.runs import new_run
    backend = type("B", (), {"check": lambda self, *a: None})()
    req = {"mission": "m", "team": [{"harness": "claude-code", "model": "openai/gpt-6.1-sol"}], "me": "vivek",
           "subscription": True, "budget": None}
    with pytest.raises(ValueError, match="calls only anthropic models"):
        new_run(req, backend, {}, "t", lambda m: m.partition("/")[0], priced=lambda m: True)


def test_start_takes_a_budget_in_dollars_or_none():
    assert cli.budget_arg("5") == 5.0 and cli.budget_arg("None") is None
    with pytest.raises(SystemExit):
        cli.start(["mission"])  # no --budget


def test_a_dry_start_on_a_server_without_dry_runs_stops_what_it_started(serve, tmp_path, monkeypatch):
    served, old = serve(), verbs.VERBS["start"]
    monkeypatch.setitem(verbs.VERBS, "start", dataclasses.replace(  # an older server, which ignores dry
        old, call=lambda server, req, token: old.call(server, {k: v for k, v in req.items() if k != "dry"}, token)))
    monkeypatch.setattr(cli, "LOGIN", str(tmp_path / "login"))
    (tmp_path / "login").write_text(json.dumps({"url": served.url, "token": served.token}))
    folder = tmp_path / "hello"
    folder.mkdir()
    (folder / "BRIEF.md").write_text("# Say hello\n")
    with pytest.raises(SystemExit, match="is older than this swarmory"):
        cli.start_run(folder, [{"harness": "claude-code"}], dry=True)
    assert [r["state"] for r in served("runs")["runs"]] == ["stopped"]


def test_a_start_refused_a_key_this_shell_has_says_to_restart_the_local_server(serve, tmp_path, monkeypatch):
    served = serve()

    def refuse(*_):  # what a server started without the key answers
        raise ValueError("The claude-code agent on its default model needs ANTHROPIC_API_KEY, which the server's "
                         "environment does not have.")
    monkeypatch.setitem(verbs.VERBS, "start", dataclasses.replace(verbs.VERBS["start"], call=refuse))
    monkeypatch.setattr(cli, "LOGIN", str(tmp_path / "login"))
    (tmp_path / "login").write_text(json.dumps({"url": served.url, "token": served.token}))
    folder = tmp_path / "hello"
    folder.mkdir()
    (folder / "BRIEF.md").write_text("# Say hello\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-dummy")
    with pytest.raises(SystemExit, match="This shell has ANTHROPIC_API_KEY, but the local server started without it"):
        cli.start_run(folder, [{"harness": "claude-code"}], dry=True)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(cli.Refused, match="needs ANTHROPIC_API_KEY"):
        cli.start_run(folder, [{"harness": "claude-code"}], dry=True)


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
    monkeypatch.setenv("SWARMORY_PROXY_PRICES", (tmp_path / "no-prices.json").as_uri())  # no network in tests
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
        said = swarmory("start", str(folder), "--hours", "0.2", "--budget", "none", "--jobs")
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


def test_the_operator_changes_a_running_team_and_the_team_hears_it(serve, monkeypatch):
    """The first agent starts alone until it opens an issue. The operator sets the budget, adds an agent and removes
    one, whose token then fails and whose issues are unassigned. The team is told once to push near the deadline and
    near the end of each budget."""
    from swarmory.control_plane import runs
    monkeypatch.setattr(runs, "STAGGER", 60)
    served = serve()
    monkeypatch.setattr(served.server.litellm, "find", lambda m: ("anthropic", {}))  # every model has a price
    run = served.start(team=[{"harness": "claude-code", "model": "claude-opus-5-5"}] * 2, budget=5)
    r, specs = served.server.runs.get(run.id), served.backend.specs
    ada = run.workers("ada")["ada"]
    assert "You start first" in (Path(served.server.data) / "runs" / run.id / "members/ada/prompt.md").read_text()
    time.sleep(1)
    assert f"{run.id}/agent/grace" not in specs, "an agent started before the lead's plan"
    run(ada, "new", title="split the work")
    grace = run.workers("grace")["grace"]
    assert "You start first" not in (Path(served.server.data) / "runs" / run.id / "members/grace/prompt.md").read_text()

    assert served("set", run=run.id, budget=20) == {"run": run.id, "budget": 20} and r.proxy.budget == 20
    assert served("set", run=run.id, budget=-1)["status"] == 400
    added = served("add", run=run.id, team=[{"harness": "claude-code", "model": "claude-opus-5-5", "role": "review"}])
    assert added["added"] == [{"id": "alan", "harness": "claude-code", "model": "claude-opus-5-5"}], added
    run.workers("alan")
    assert served("add", run=run.id, team=[{"harness": "claude-code", "name": "Ada"}])["status"] == 400

    run(grace, "assign", id="i1", add=["me"])
    run(grace, "move", id="i1", status="doing")
    assert served("remove", run=run.id, members=["grace"]) == {"run": run.id, "removed": ["grace"]}
    assert f"{run.id}/agent/grace" not in served.backend.live() and run(grace, "me")["status"] == 401
    i1 = run(ada, "issue", id="i1")["issue"]
    assert (i1["assignees"], i1["status"]) == ([], "todo")
    assert run(ada, "assign", id="i1", add=["grace"])["status"] == 400
    workers = {m["id"]: m["worker"] for m in served("status", run=run.id)["members"]}
    assert workers == {"ada": "running", "grace": "removed", "vivek": None, "alan": "running"}, workers

    r.proxy.totals = lambda jobs=False: {"ada": {"usd": 18.5}}
    for _ in range(2):
        r.warn(r.config["deadline"] - 300)
    said = [m["text"] for m in run(ada, "read", conv="general", all=True)["messages"] if m["author"] == "swarmory"]
    assert said[0].startswith("@all The operator set the run's budget to $20.00")
    assert said[1].startswith("@all New on the team: Alan (alan), claude-code, role: review.")
    assert said[2] == "@all The operator took Grace (grace) off the team. Their open issues are unassigned: " \
                      "i1 split the work."
    assert said[3].startswith("@all The run ends in about 5 minutes.") and "$18.50 of its $20.00" in said[4]
    assert len(said) == 5, said
    assert run(grace, "me")["status"] == 401
