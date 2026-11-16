# /// script
# requires-python = ">=3.11"
# dependencies = ["modal==1.6.3"]
# ///
"""The feature end-to-end test: a run of tests/features, whose brief gives four agents exact commands that touch every
part of a run, and a second, small run whose $0.002 budget must stop it. Every minute the test downloads both runs'
exports, lays out their records (tools/report.py's records()), adds driver.json (what only it knows), and runs
tests/check_features.py on them.

  - Three agents run on another provider's model through the server's LiteLLM; every call must be priced.
  - Each agent gets a grant, a secret made for this test, and must hold nothing else but its own SWARMORY_TOKEN.
  - On Modal, agents may reach example.com (--allow) and no other outside host.
  - Once every agent has said done, the test kills the server as a crash would, and locally starts it again on the
    same data (Modal starts its one container again). Then it tells the team `phase two`, and each agent downloads a
    file and fetches main again from the restarted server.
  - The watch link must load, and must serve every agent's session trace.

It passes once every check does, and fails at 45 minutes with the checks that still fail.

    uv run tests/e2e_features.py            its own local server, on a fresh data folder and a free port; its jobs'
                                            trials run in Docker, which must be running
    uv run tests/e2e_features.py --modal    deploys the server, then runs on the Modal server `swarmory login` points
                                            at; needs the Modal secret swarmory-modal, and makes swarmory-feature-grant

Model calls use the server's ANTHROPIC_API_KEY, OPENAI_API_KEY and GEMINI_API_KEY (locally, your shell's): Linus runs
on a Gemini model. With E2E_SUBSCRIPTION set they use its subscription logins, which cannot call another provider's
model: each agent then runs on its own provider's model, the two translated jobs are left out, and nothing checks
translation.
"""
import hashlib
import io
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tools")]
from check_features import lines  # noqa: E402
from report import records as lay_out  # noqa: E402

GRANT = "swarmory-feature-grant"  # the Modal secret that holds FEATURE_SECRET on a Modal run
MINUTES = 45
SMALL = 0.002  # the small run's budget: Haiku spends it in a few calls, before the mission is done
SUBSCRIPTION = bool(os.environ.get("E2E_SUBSCRIPTION"))
OWN = {"claude-code": "claude-haiku-5-5", "codex": "gpt-6.1-sol"}  # each harness's own provider's model
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
DATA = Path(tempfile.mkdtemp(prefix="e2e-features."))  # locally the server's data; on Modal the exports
ENV = {**os.environ, "PYTHONPATH": str(REPO)}  # this checkout's swarmory, for `swarmory` and a local server


def say(text: str):
    print(f"e2e-features {time.strftime('%H:%M:%S')} {text}", flush=True)


def swarmory(*args, check=True) -> str:
    p = subprocess.run([sys.executable, "-m", "swarmory.cli", *args], cwd=REPO, capture_output=True, text=True, env=ENV)
    if check and p.returncode:
        sys.exit(f"`swarmory {' '.join(args)}` failed:\n{p.stdout}{p.stderr}")
    return p.stdout


def login() -> dict:
    """The server's URL and the operator token, from the login file."""
    path = Path(ENV.get("SWARMORY_LOGIN") or Path.home() / ".swarmory" / "login")
    if not path.is_file():
        sys.exit("Log in to the server first: swarmory login URL TOKEN")
    return json.loads(path.read_text())


def sha16(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def get(url: str, token: str, timeout: float = 60):
    """(status, body bytes or None) of a GET with a token."""
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, None
    except OSError:
        return None, None


def up(server: dict, minutes: float = 5) -> bool:
    end = time.time() + minutes * 60
    while time.time() < end:
        if get(server["url"] + "/health", "")[0] == 200:
            return True
        time.sleep(5)
    return False


class Local:
    """A local server on DATA, which this test starts and kills. Its environment holds FEATURE_SECRET, the grant."""

    def __init__(self, secret: str):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.token = secrets.token_hex(16)
        ENV["SWARMORY_LOGIN"] = str(DATA / "login")  # the person's own login stays as it is
        self.env = {**ENV, "SWARMORY_OPERATOR_TOKEN": self.token, "FEATURE_SECRET": secret}
        self.start()
        swarmory("login", f"http://127.0.0.1:{self.port}", self.token)

    def start(self):
        log = open(DATA / "server.log", "ab")
        self.proc = subprocess.Popen([sys.executable, "-m", "swarmory.cli", "serve", "--data", str(DATA), "--port",
                                      str(self.port)], env=self.env, stdout=log, stderr=log)

    def crash(self):
        self.proc.send_signal(signal.SIGKILL)
        self.proc.wait()
        self.start()

    def close(self):
        self.proc.terminate()


class Modal:
    """The deployed server `swarmory login` points at. A crash stops its container, which Modal starts again."""

    def crash(self):
        rows = json.loads(subprocess.run(["uvx", "modal", "container", "list", "--json"], capture_output=True,
                                         text=True, check=True).stdout)
        pick = lambda r, *words: next((v for k, v in r.items() if all(w in k.lower() for w in words)), None)  # noqa
        for r in rows:
            if str(pick(r, "app") or "") == "swarmory":
                subprocess.run(["uvx", "modal", "container", "stop", pick(r, "container", "id")], check=True)

    def close(self):
        pass


def start(mission: Path, *more) -> dict:
    args = ["start", str(mission), *more]
    if SUBSCRIPTION:
        args.append("--subscription")
    out = swarmory(*args)
    rid = next(line.split()[1].rstrip(":") for line in out.splitlines() if line.startswith("run "))
    say(f"run {rid}")
    link = next(line.split()[1] for line in swarmory("status", rid).splitlines() if line.startswith("watch "))
    return {"run": rid, "view": link.partition("#")[2], "base": link.partition("/view#")[0]}


def view(run: dict, path: str):
    """(status, JSON body or None) of a viewer route, with the run's view token."""
    status, body = get(run["base"] + path, run["view"], 20)
    try:
        return status, json.loads(body) if body else None
    except ValueError:
        return status, None


def records(server: dict, run: dict, extra: dict) -> Path:
    """The run's export, unpacked in DATA/export/RUN and laid out in its records/, with driver.json."""
    status, body = get(f"{server['url']}/r/{run['run']}/export", server["token"], 300)
    folder = DATA / "export" / run["run"]
    if status != 200 or not body:
        return folder / "records"
    shutil.rmtree(folder, ignore_errors=True)
    with tarfile.open(fileobj=io.BytesIO(body)) as tar:
        tar.extractall(DATA / "export", filter="data")
    try:
        out = lay_out(folder)
    except SystemExit:  # no main yet
        return folder / "records"
    (out / "driver.json").write_text(json.dumps(extra, indent=1))
    return out


def said(path: Path, prefix: str) -> list:
    return [e for e in lines(path / "server" / "log.jsonl")
            if e.get("type") == "message" and e["text"].startswith(prefix)]


def budget_outcome(path: Path) -> dict:
    """The budget run: what its ledger says it spent, and whether its agent was told the run's budget is spent."""
    rows = lines(path / "server" / "ledger.jsonl")
    told = any("The run has spent" in f.read_text(errors="replace")
               for f in path.glob("agents/*/sessions/*.jsonl")) if path.is_dir() else False
    return {"usd": round(sum(r.get("usd") or 0 for r in rows), 6), "calls": len(rows), "refused": told}


def main():
    if set(sys.argv[1:]) - {"--modal"}:
        sys.exit(__doc__)
    modal = "--modal" in sys.argv
    secret = secrets.token_hex(16)  # FEATURE_SECRET: made for this test, so seeing it proves only the grant works
    if modal:
        swarmory("deploy")
        subprocess.run([sys.executable, "-m", "modal", "secret", "create", "--force", GRANT,
                        f"FEATURE_SECRET={secret}"], check=True, capture_output=True)
        host = Modal()
        if not login()["url"].startswith("https://"):
            sys.exit("Log in to the Modal server first: swarmory login URL TOKEN")
    else:
        host = Local(secret)
    server = login()
    if not up(server):
        sys.exit(f"The server at {server['url']} does not answer; see {DATA}.")
    mission = Path(tempfile.mkdtemp(prefix="features."))
    shutil.copytree(REPO / "tests" / "features", mission, dirs_exist_ok=True)
    shutil.copytree(REPO / "tests" / "eval-task" / "hello", mission / "tasks" / "hello")
    if SUBSCRIPTION:  # no translation: every agent on its harness's own model, and no job on another provider's
        team = (("claude-code", "Ada"), ("codex", "Grace"), ("claude-code", "Linus"), ("claude-code", "Turing"))
        (mission / "team.toml").write_text("".join(
            f'[[agent]]\nharness = "{h}"\nname = "{n}"\nmodel = "{OWN[h]}"\n\n' for h, n in team))
        brief = (mission / "BRIEF.md").read_text().splitlines(keepends=True)
        (mission / "BRIEF.md").write_text("".join(x for x in brief if "--model openai/" not in x
                                                  and "--model anthropic/" not in x))
    grant = ["--grant", GRANT, "--allow", "example.com"] if modal else ["--grant", "FEATURE_SECRET"]
    run = start(mission, "--hours", "0.75", "--budget", "20", "--jobs", *grant)
    small = start(REPO / "tests" / "mission", "--hours", "0.3", "--budget", str(SMALL), "--team",
                  "claude-code:claude-haiku-5-5=1")
    swarmory("use", run["run"])
    extra = {"grant": sha16(secret)}
    folder = DATA / "export" / run["run"] / "records"
    check = [sys.executable, str(REPO / "tests" / "check_features.py"), str(folder)] + ([] if modal else ["--local"]) \
        + (["--subscription"] if SUBSCRIPTION else [])
    end, last, out = time.time() + MINUTES * 60, "", ""
    try:
        while time.time() < end:
            time.sleep(60)
            extra["budget_run"] = {**budget_outcome(records(server, small, {})), "budget": SMALL}
            path = records(server, run, extra)
            team = [m["id"] for m in json.loads((path / "run.json").read_text())["team"]] \
                if (path / "run.json").is_file() else []
            if not extra.get("restart_at"):  # traces and the viewer, read while the first server is up
                extra["view"] = view(run, "/view")[0]
                extra["traces"] = {a: len((view(run, f"/trace?member={a}")[1] or {}).get("events", ())) for a in team}
            if team and not extra.get("restart_at") and len({m["author"] for m in said(path, "done ")}) == len(team):
                say("every agent is done: killing the server")
                host.crash()
                extra["restart_at"] = time.time()
                if not up(server):
                    say("FAIL: the server did not come back in 5 minutes")
                    break
                swarmory("send", "general", "@all phase two")
                say("the server is back: told the team phase two")
                path = records(server, run, extra)
            if not path.is_dir():
                continue
            p = subprocess.run(check, capture_output=True, text=True)
            out = p.stdout + p.stderr
            if p.returncode == 0:
                print(out, flush=True)
                print(swarmory("status", run["run"], check=False), flush=True)
                return say("PASS")
            now = out.strip().splitlines()[-1] if out.strip() else ""
            if now != last:
                say(now)
                last = now
        print(out, flush=True)
        say(f"FAIL: not every check passed in {MINUTES} minutes (data: {DATA})")
        sys.exit(1)
    finally:
        swarmory("stop", run["run"], check=False), swarmory("stop", small["run"], check=False)
        host.close()


if __name__ == "__main__":
    main()
