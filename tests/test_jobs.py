"""Jobs: a member asks with `swarmory job`, which sends a commit and a path; the server records the folder's git tree,
keeps the job in its run's log, claims it, and starts a job worker (swarmory/worker/job.py), which runs Harbor with only
its listed environment, holding the job's own token and never the operator's. Here the backend is Stub, so a test runs
the worker itself with the tokens the server minted for it, and Harbor is a stand-in that writes what Harbor writes;
tests/e2e_evals.sh runs the real one."""
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import code, soon

from swarmory.control_plane import jobs as jobslib
from swarmory.control_plane import team as teams

FAKE_HARBOR = '''import json, os, pathlib, sys, time
args = sys.argv[1:]
arg = lambda flag: args[args.index(flag) + 1]
task, out, job, k = pathlib.Path(arg("-p")), pathlib.Path(arg("-o")), arg("--job-name"), int(arg("-k"))
harness = "--ak" in args and pathlib.Path(arg("--ak").split("=", 1)[1])
(out / "seen.json").write_text(json.dumps({"argv": args, "pid": os.getpid(), "env": dict(os.environ),
    "task": sorted(p.name for p in task.iterdir()), "toml": (task / "task.toml").read_text(),
    "harness": harness and {p.name: p.read_text() for p in harness.iterdir()}}))
if (task / "slow").exists():
    time.sleep(60)
for i in range(k):
    trial = out / job / f"{task.name}__{i}"
    trial.mkdir(parents=True)
    result = {"verifier_result": {"rewards": {"reward": 1.0 if i == 0 else 0.0}}}
    if i == 1:
        result["exception_info"] = {"exception_type": "AgentTimeoutError", "exception_message": "took too long"}
    (trial / "result.json").write_text(json.dumps(result))
print("done", k)
'''
TASK = '[task]\nname = "t/hello"\n\n[environment]\nnetwork_mode = "no-network"\ncpus = 1\n\n[agent]\n' \
       'timeout_sec = 600.0\n\n[verifier.env]\nMODE = "strict"\n'


def git(cwd: Path, *args) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def task(tmp: Path, name="hello", toml=TASK) -> Path:
    """A git repo at tmp/work with one committed Harbor task folder, tasks/<name>, which is returned."""
    work = tmp / "work"
    if not work.exists():
        work.mkdir()
        git(work, "init", "-q", "-b", "main")
    t = work / "tasks" / name
    (t / "tests").mkdir(parents=True)
    (t / "task.toml").write_text(toml)
    (t / "tests" / "test.sh").write_text("echo 1 > /logs/verifier/reward.txt\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", f"task {name}")
    return t


def commit(tmp: Path, files: dict) -> str:
    """Commits files, by their path in tmp/work, and returns the commit."""
    for name, text in files.items():
        (tmp / "work" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp / "work" / name).write_text(text)
    git(tmp / "work", "add", "-A")
    git(tmp / "work", "commit", "-q", "-m", "files")
    return git(tmp / "work", "rev-parse", "HEAD")


RUN = "#!/bin/sh\necho hello > /app/output/answer.txt\n"


def queue(tmp: Path):
    """A team of ada, grace and the operator vivek, and its jobs, on tmp, as a local server has them, with a
    repo that holds tmp/work's HEAD."""
    members = [{"id": x, "name": x.title(), "kind": "agent", "token_sha256": hashlib.sha256(x.encode()).hexdigest()}
               for x in ("ada", "grace", "vivek")]
    members[-1].update(kind="human", operator=True)
    (tmp / "config.json").write_text(json.dumps({"run": "t-1", "mission": "t", "deadline": 2e9, "members": members,
                                                 "channels": [{"id": "general", "about": "all"}],
                                                 "view_sha256": hashlib.sha256(b"view").hexdigest()}))
    repo = tmp / "work.git"
    if not repo.exists():
        git(tmp, "init", "-q", "--bare", "-b", "main", str(repo))
    if (tmp / "work").exists():
        git(tmp / "work", "push", "-q", str(repo), "HEAD:refs/heads/main")
    jobs = jobslib.Jobs(repo, True, 2e9, "http://127.0.0.1:7700")
    jobs.team = teams.Team(tmp, jobs.apply)
    return jobs.team, jobs


class Upstream(BaseHTTPRequestHandler):  # stands in for Anthropic, for the server's gateway
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.server.seen.append(self.headers.get("x-api-key"))
        body = json.dumps({"model": "m", "usage": {"input_tokens": 7, "output_tokens": 3}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def jobs_run(serve, tmp_path, monkeypatch):
    """A run with jobs on the one server, its model gateway pointed at a stand-in upstream; ada and grace are agents,
    vivek the operator."""
    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    up.seen = []
    threading.Thread(target=up.serve_forever, daemon=True).start()
    monkeypatch.setenv("SWARMORY_PROXY_ANTHROPIC", f"http://127.0.0.1:{up.server_port}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real-key")
    served = serve()
    run = served.start(team=[{"harness": "claude-code"}] * 2, jobs=True)
    tokens = {**run.workers("ada", "grace"), "vivek": served.token}

    def api(who, verb, **req):
        """A run verb as a member (its id), or with any other token as given."""
        return run(tokens.get(who, who), verb, **req)

    def swarmory(who, *args, cwd=tmp_path):
        login = tmp_path / f"login-{who}"
        login.write_text(json.dumps({"url": run.url, "token": tokens[who]}))
        r = subprocess.run([sys.executable, "-m", "swarmory.cli", *args], capture_output=True, text=True, cwd=cwd,
                           env={**os.environ, "SWARMORY_LOGIN": str(login)})
        return r.returncode, r.stdout + r.stderr

    def claimed(jid) -> dict:
        """The job the server claimed, with the two tokens it gave the job's worker."""
        name = f"{run.id}/job/{jid}"
        assert soon(lambda: name in served.backend.specs), f"the server started no worker for {jid}"
        env = served.backend.specs[name].env
        return {"token": env["SWARMORY_TOKEN"], "spend": env["SWARMORY_SPEND"],
                "job": api("ada", "job-show", id=jid)["job"]}

    def worker(claimed) -> str:
        """Runs the job worker for a claimed job, with a stand-in Harbor, in an environment full of what it must not
        pass on. Returns its output."""
        fake = tmp_path / "fake_harbor.py"
        fake.write_text(FAKE_HARBOR)
        env = {**os.environ, "SWARMORY_URL": run.url, "SWARMORY_TOKEN": claimed["token"],
               "SWARMORY_SPEND": claimed["spend"], "SWARMORY_JOB": claimed["job"]["id"],
               "HARBOR": f"{sys.executable} {fake}",
               "ANTHROPIC_API_KEY": "real-key", "MODAL_TOKEN_ID": "modal-id", "MODAL_TOKEN_SECRET": "modal-secret",
               "UNLISTED": "not-for-harbor"}
        p = subprocess.run([sys.executable, "-m", "swarmory.worker.job"], env=env, cwd=tmp_path, timeout=60,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert p.returncode == 0, p.stdout
        return p.stdout

    def artifacts(who, job_id) -> dict:
        """The files of a job's artifacts, by their path in the archive, as a member downloads them."""
        fid = api(who, "job-show", id=job_id)["job"]["artifacts"]
        r = urllib.request.Request(f"{run.url}/files/{fid}", headers={"Authorization": f"Bearer {tokens[who]}"})
        with tarfile.open(fileobj=io.BytesIO(urllib.request.urlopen(r, timeout=30).read())) as tar:
            return {m.name: tar.extractfile(m).read().decode() for m in tar.getmembers() if m.isfile()}

    api.url, api.swarmory, api.upstream, api.worker, api.artifacts = run.url, swarmory, up, worker, artifacts
    api.claimed = claimed
    api.tokens = tokens
    api.code = lambda path, token, body=None, basic=False: code(run.url + path, tokens.get(token, token), body, basic)
    yield api
    up.shutdown()


def test_a_canceled_job_stays_canceled_and_its_trials_spend_no_more_while_its_worker_can_still_report(tmp_path):
    folder = task(tmp_path)
    t, jobs = queue(tmp_path)
    jobs.submit("ada", {"commit": git(folder, "rev-parse", "HEAD"), "path": "tasks/hello",
                        "params": {"agent": "nop"}, "to": "grace"})
    claimed = jobs.claim("j1")
    assert jobs.cancel("ada", {"id": "j1"})["job"]["status"] == "canceled"
    assert jobs.auth(claimed["spend"]) is None and jobs.auth(claimed["token"])[0] == "job"
    with pytest.raises(ValueError):
        jobs.cancel("vivek", {"id": "j1"})
    assert jobs.finish("ada", {"id": "j1", "status": "done", "headline": "0 of 1"})["job"]["status"] == "canceled", \
        "the worker's report keeps the job canceled"
    assert "was canceled" in t.convs["dm:ada:grace"][-1]["text"]
    assert queue(tmp_path)[1].show("grace", {"id": "j1"})["job"]["status"] == "canceled"


def test_a_task_toml_that_is_not_sealed_or_has_a_template_in_an_env_table_is_refused_when_the_job_is_asked_for(
        tmp_path):
    task(tmp_path, "leak", TASK.replace('"strict"', '"${MODAL_TOKEN_SECRET}"'))
    task(tmp_path, "open", TASK.replace("no-network", "public"))
    task(tmp_path, "plain", TASK.replace('"strict"', '"$5 {each}"'))
    t, jobs = queue(tmp_path)
    ask = {"commit": git(tmp_path / "work", "rev-parse", "HEAD"), "params": {"agent": "oracle"}}
    for path in ("tasks/leak", "tasks/open", "tasks"):  # a folder of tasks: each is checked
        with pytest.raises(ValueError):
            jobs.submit("ada", {**ask, "path": path})
    assert jobs.submit("ada", {**ask, "path": "tasks/plain"})["job"]["status"] == "queued"
    assert not t.convs["general"][:-1], "a refused job is never queued"


def test_an_agent_folder_is_a_committed_harness_with_a_run_and_no_task_has_a_compose_file(tmp_path):
    task(tmp_path)
    task(tmp_path, "composed")
    head = commit(tmp_path, {"agents/h/run": RUN, "agents/bare/README": "no run", "tasks/composed/tests/"  # a separate verifier's
                             "docker-compose.yaml": "services:\n  main:\n    environment: [X=${MODAL_TOKEN_SECRET}]\n"})
    t, jobs = queue(tmp_path)
    ask = {"commit": head, "path": "tasks/hello"}
    job = jobs.submit("ada", {**ask, "params": {"agent": "agents/h/", "model": "claude-haiku-5-5"}})["job"]
    assert job["params"]["agent"] == "agents/h" and job["harness"] == git(tmp_path / "work", "rev-parse",
                                                                          "HEAD:agents/h"), "it names what ran"
    for params in ({"agent": "agents/bare", "model": "m"}, {"agent": ".", "model": "m"}, {"agent": "/", "model": "m"},
                   {"agent": "agents.h:Committed", "model": "m"}, {"agent": "agents/none", "model": "m"},
                   {"agent": "../agents/h", "model": "m"}, {"agent": "agents/h"}):
        with pytest.raises(ValueError):
            jobs.submit("ada", {**ask, "params": params})
    for agent in ("", ".", "Claude Code"):  # not a name Harbor could have
        with pytest.raises(ValueError, match="one of Harbor's agents"):
            jobs.submit("ada", {**ask, "params": {"agent": agent, "model": "m"}})
    with pytest.raises(ValueError, match="docker-compose.yaml"):
        jobs.submit("ada", {**ask, "path": "tasks", "params": {"agent": "oracle"}})
    assert jobs.submit("ada", {**ask, "params": {"agent": "oracle"}})["job"]["harness"] is None


def test_any_harbor_agent_takes_its_options_and_a_job_still_keeps_credentials_and_its_sandboxes_its_own(tmp_path):
    from swarmory import harbor
    task(tmp_path)
    head = commit(tmp_path, {"README.md": "tasks\n"})
    t, jobs = queue(tmp_path)
    ask = {"commit": head, "path": "tasks/hello"}
    params = {"agent": "terminus-2", "model": "anthropic/claude-haiku-5-5", "attempts": "3", "concurrent": "16",
              "agent_timeout_multiplier": "2.5", "ak": ["max_turns=40"], "ae": ["DEBUG=1"],
              "ek": ['keepalive=["/usr/local/sbin/start-services"]']}
    job = jobs.submit("ada", {**ask, "params": params})["job"]
    argv = harbor.command({**job, "tag": "t"}, tmp_path, tmp_path / "out", "https://s.example/llm", "k")[0]
    joined = " ".join(argv)
    for flag in ("-a terminus-2", "-n 16", "--agent-timeout-multiplier 2.5", "--ak max_turns=40", "--ae DEBUG=1",
                 '--ek keepalive=["/usr/local/sbin/start-services"]', "--ae GEMINI_API_KEY=${SWARMORY_JOB_KEY}"):
        assert flag in joined, flag
    for bad in ({"ae": ["X=${MODAL_TOKEN_SECRET}"]}, {"ae": ["ANTHROPIC_API_KEY=mine"]},
                {"ae": ["OPENAI_BASE_URL=https://elsewhere"]}, {"ek": ["secrets=[\"keys\"]"]},
                {"ek": ["labels={}"]}, {"ek": ["volumes={}"]}, {"ak": ["novalue"]}, {"concurrent": "65"},
                {"timeout_multiplier": "0"}, {"agent": "oracle", "model": "m"}, {"agent": "gemini-cli", "model": None}):
        with pytest.raises(ValueError):
            jobs.submit("ada", {**ask, "params": {**params, **bad}})


def test_a_harness_runs_in_harbor_through_the_adapter_alone_with_every_gateway_route(jobs_run, tmp_path):
    from swarmory import harbor
    folder = task(tmp_path)
    commit(tmp_path, {"agents/h/run": RUN})
    (tmp_path / "work" / "agents" / "h" / "run").write_text("#!/bin/sh\nexit 1\n")
    asked = jobs_run.swarmory("ada", "job", "harbor", ".", "--agent", "agents/h", "--model", "claude-haiku-5-5",
                              cwd=folder)  # its path from the repo's root
    assert asked[0] != 0 and "agents/h has uncommitted changes" in asked[1], asked
    git(tmp_path / "work", "checkout", "--", "agents")
    asked = jobs_run.swarmory("ada", "job", "harbor", ".", "--agent", "../../agents/h", "--model", "claude-haiku-5-5",
                              cwd=folder)
    assert asked[0] == 0, asked
    claimed = jobs_run.claimed("j1")
    assert claimed["job"]["params"]["agent"] == "agents/h", "sent as its path in the repo"
    assert jobs_run.worker(claimed).strip().endswith("worker: j1 done")
    seen = json.loads(jobs_run.artifacts("ada", "j1")["job-j1/seen.json"])
    assert seen["harness"] == {"run": RUN} and seen["task"] == ["task.toml", "tests"]
    a = seen["argv"]
    assert a[a.index("-a") + 1] == "committed:Committed" and a[a.index("--ak") + 1].startswith("dir=")
    assert seen["env"]["PYTHONPATH"] == str(harbor.ADAPTER) and (harbor.ADAPTER / "committed.py").exists(), \
        "Harbor imports the adapter and nothing else of swarmory's"
    assert seen["env"].keys() - {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"} == {
        "PATH", "HOME", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "SWARMORY_JOB_KEY", "PYTHONPATH"}
    gateway = jobs_run.url.replace("127.0.0.1", "host.docker.internal") + "/llm"
    assert a[a.index("--ae"):] == ["--ae", f"ANTHROPIC_BASE_URL={gateway}/anthropic",
                                   "--ae", f"ANTHROPIC_API_BASE={gateway}/anthropic",
                                   "--ae", "ANTHROPIC_API_KEY=${SWARMORY_JOB_KEY}",
                                   "--ae", f"OPENAI_BASE_URL={gateway}/openai/v1",
                                   "--ae", f"OPENAI_API_BASE={gateway}/openai/v1",
                                   "--ae", "OPENAI_API_KEY=${SWARMORY_JOB_KEY}",
                                   "--ae", f"GOOGLE_GEMINI_BASE_URL={gateway}/gemini",
                                   "--ae", "GEMINI_API_KEY=${SWARMORY_JOB_KEY}",
                                   "--allow-environment-host", "host.docker.internal"]
    assert seen["env"]["SWARMORY_JOB_KEY"] == claimed["spend"]


def test_a_job_token_opens_only_its_own_job_and_a_spend_token_only_model_calls(jobs_run, tmp_path):
    folder = task(tmp_path)
    for _ in range(2):
        asked = jobs_run.swarmory("ada", "job", "harbor", "tasks/hello", "--agent", "nop", cwd=folder.parents[1])
        assert asked[0] == 0, asked
    jobs_run("ada", "send", to="grace", text="the key is under the mat")
    one, two = jobs_run.claimed("j1"), jobs_run.claimed("j2")
    token, spend = one["token"], one["spend"]

    assert jobs_run(token, "job-show", id="j1")["job"]["id"] == "j1"
    assert jobs_run.code("/job/input?id=j1", token) == 200
    for verb, req in [("read", {"conv": "dm:ada:grace"}), ("read", {"all": True}), ("me", {}),
                      ("send", {"to": "general", "text": "hi"}), ("spend", {}), ("jobs", {}),
                      ("job-show", {"id": "j2"}), ("finish", {"id": "j2", "status": "failed", "error": "mine now"}),
                      ("cancel", {"id": "j1"}), ("claim", {}), ("job", {})]:
        assert jobs_run(token, verb, **req)["status"] == 403, f"a job token did {verb}"
    assert [jobs_run.code(p, token) for p in ("/job/input?id=j2", "/feed", "/llm/anthropic/v1/messages")] \
        == [403, 403, 401]
    assert jobs_run.code("/git/work.git/info/refs?service=git-upload-pack", token, basic=True) == 403
    assert jobs_run("ada", "job-show", id="j2")["job"]["status"] == "running", "j2 is untouched"

    assert jobs_run.code("/llm/anthropic/v1/messages", spend, b"{}") == 200
    assert jobs_run.upstream.seen == ["real-key"], "a trial's call gets the real key"
    for verb, req in [("job-show", {"id": "j1"}), ("finish", {"id": "j1", "status": "done"}), ("read", {}),
                      ("jobs", {})]:
        assert jobs_run(spend, verb, **req)["status"] == 403, f"a spend token did {verb}"
    assert [jobs_run.code(p, spend) for p in ("/job/input?id=j1", "/files/f1", "/feed", "/commits")] == [403] * 4
    assert [jobs_run.code(p, spend, b"x") for p in ("/files?name=x", "/trace?name=x")] == [403, 403]
    assert jobs_run.code("/git/work.git/info/refs?service=git-upload-pack", spend, basic=True) == 403

    assert jobs_run("ada", "claim")["status"] == 404, "the server claims jobs itself"
    assert jobs_run("grace", "cancel", id="j1")["status"] == 403
    assert jobs_run("grace", "finish", id="j1", status="done")["status"] == 403
    assert jobs_run(token, "finish", id="j1", status="failed", error="it broke")["job"]["status"] == "failed"
    assert jobs_run.code("/llm/anthropic/v1/messages", spend, b"{}") == 401, "the spend token ends with the job"
    assert jobs_run(token, "job-show", id="j1")["status"] == 401, "and so does the job token"
    assert jobs_run("ada", "job-show", id="j1")["job"]["used"]["calls"] == 1, "the trial's call was billed to it"
    assert jobs_run(two["spend"], "job-show", id="j2")["status"] == 403


def test_harbor_sees_only_its_listed_environment_and_the_committed_folder(jobs_run, tmp_path):
    folder = task(tmp_path)
    asked = jobs_run.swarmory("ada", "job", "harbor", "hello", "--agent", "claude-code", "--model", "claude-haiku-5-5",
                              "--attempts", "2", cwd=folder.parent)
    assert asked[0] == 0, asked
    claimed = jobs_run.claimed("j1")
    assert jobs_run.worker(claimed).strip().endswith("worker: j1 done")
    files = jobs_run.artifacts("ada", "j1")
    assert {"job-j1/j1/hello__0/result.json", "job-j1/harbor.log"} <= set(files)
    seen = json.loads(files["job-j1/seen.json"])
    assert seen["task"] == ["task.toml", "tests"] and seen["toml"] == TASK, "Harbor ran on the folder as committed"
    assert seen["env"].keys() - {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"} == {
        "PATH", "HOME", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "SWARMORY_JOB_KEY"}, "Harbor's environment is a list"
    assert seen["env"]["SWARMORY_JOB_KEY"] == claimed["spend"] and seen["env"]["MODAL_TOKEN_SECRET"] == "modal-secret"
    for secret in (claimed["token"], jobs_run.tokens["vivek"], "real-key", "not-for-harbor"):
        assert secret not in json.dumps(seen), "Harbor got a token or a key it has no use for"
    gateway = jobs_run.url.replace("127.0.0.1", "host.docker.internal") + "/llm/anthropic"
    a = seen["argv"]
    assert a[a.index("-m"):a.index("-m") + 2] == ["-m", "claude-haiku-5-5"]
    assert f"ANTHROPIC_BASE_URL={gateway}" in a and "ANTHROPIC_API_KEY=${SWARMORY_JOB_KEY}" in a
    assert a[-2:] == ["--allow-environment-host", "host.docker.internal"], \
        "a local server's trials run in Docker, and reach it"
    job = jobs_run("ada", "job-show", id="j1")["job"]
    assert job["status"] == "done" and job["outcome"]["headline"] == "1 passed, 0 failed, 1 errored of 2"


def test_harbor_runs_up_to_8_trials_at_once_retries_a_start_uses_the_images_harbor_and_counts_errors_apart(
        monkeypatch, tmp_path):
    from swarmory import harbor
    monkeypatch.delenv("HARBOR", raising=False)

    def argv(env, **params):
        job = {"id": "j1", "tag": "claim-tag",
               "params": {"agent": "oracle", "model": None, "env": env, "attempts": 25, "tasks": 4, **params}}
        return harbor.command(job, tmp_path, tmp_path / "out", "https://s.example/llm", "k")[0]

    def flag(a, name):
        return a[a.index(name) + 1]

    a = argv("modal")
    assert flag(a, "-n") == "8" and flag(a, "-k") == "25"
    assert flag(argv("modal", attempts=1, tasks=3), "-n") == "3", "a small job runs every trial at once"
    assert flag(a, "--max-retries") == "1" and flag(a, "--retry-include") == "EnvironmentStartTimeoutError"
    assert a[:4] == ["uvx", "--from", harbor.VERSION, "harbor"], "with no Harbor of its own, the pinned one"
    monkeypatch.setenv("HARBOR", "harbor")
    assert argv("modal")[:2] == ["harbor", "run"], "the job image's own Harbor, with no download"

    for name, result in (("t__0", {"verifier_result": {"rewards": {"reward": 1.0}}}),
                         ("t__1", {"verifier_result": {"rewards": {"reward": 0.0}}}),
                         ("t__2", {"exception_info": {"exception_type": "EnvironmentStartTimeoutError",
                                                      "exception_message": "timed out\nafter 600 s"}})):
        (tmp_path / "out" / name).mkdir(parents=True)
        (tmp_path / "out" / name / "result.json").write_text(json.dumps(result))
    report = harbor.report(tmp_path / "out", 0, tmp_path / "none.log")
    assert report["headline"] == "1 passed, 1 failed, 1 errored of 3"
    job = {"id": "j1", "status": "done", "outcome": {**report, "error": None}, "artifacts": None,
           "params": {"agent": "oracle"}, "path": "tasks", "commit": "abcdef0"}
    assert jobslib.verdict(job).endswith("\n- t__2: EnvironmentStartTimeoutError: timed out"), jobslib.verdict(job)
