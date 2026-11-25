"""The processes tests start import this checkout's swarmory and the helpers in tests/, as the tests themselves do
(pyproject.toml's pythonpath). `serve` starts the one server in this process, on a temporary data directory, with
Stub as its backend, whose workers run nowhere and whose specs a test reads to act as them. No model is called: every
*_API_KEY is a dummy."""
import base64
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from swarmory.seed import make_seed

TESTS = Path(__file__).resolve().parent
os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (str(TESTS.parent), str(TESTS), os.environ.get("PYTHONPATH"))))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def soon(check, within=10):
    """check()'s first truthy answer, asked again until `within` seconds pass."""
    end = time.monotonic() + within
    while not (ok := check()) and time.monotonic() < end:
        time.sleep(0.05)
    return ok


def post(url: str, token: str, verb: str, **req) -> dict:
    """A verb's data, or {"status", "error"} when the server refuses it."""
    r = urllib.request.Request(f"{url}/api/{verb}", json.dumps(req).encode(), {"Authorization": f"Bearer {token}"})
    try:
        with OPENER.open(r, timeout=60) as got:
            return json.loads(got.read())["data"]
    except urllib.error.HTTPError as e:
        return {"status": e.code, **json.loads(e.read() or b"{}")}


def code(url: str, token: str = "", body: bytes = None, basic: bool = False) -> int:
    """The status a raw request gets, its token as a Bearer, or as git's Basic password."""
    auth = ("Basic " + base64.b64encode(f"swarmory:{token}".encode()).decode()) if basic else f"Bearer {token}"
    try:
        with OPENER.open(urllib.request.Request(url, body, {"Authorization": auth} if token else {}), timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


class Stub:
    """A backend whose workers run nowhere. It keeps each spec it was asked to start, so a test can act as the worker
    with the token the server minted for it, and a worker it started stays live until it is stopped."""
    kind = "local"

    def __init__(self):
        self.specs, self.up = {}, set()

    def start(self, spec):
        self.specs[spec.name] = spec
        self.up.add(spec.name)

    def live(self):
        return frozenset(self.up)

    def stop(self, name):
        self.up.discard(name)

    def check(self, harnesses, grants, allow):
        pass


class Run:
    """A test's view of one run: its URL, and the tokens its workers were given."""

    def __init__(self, served, data: dict):
        self.served, self.id, self.url, self.view = served, data["run"], data["url"], data["view"].rsplit("#")[1]

    def __call__(self, token: str, verb: str, **req):
        """A run verb's data, as whoever holds the token."""
        return post(self.url, token, verb, **req)

    def workers(self, *ids) -> dict:
        """Each agent's token, once the server has started its worker."""
        specs = self.served.backend.specs
        assert soon(lambda: all(f"{self.id}/agent/{i}" in specs for i in ids)), "no workers"
        return {i: specs[f"{self.id}/agent/{i}"].env["SWARMORY_TOKEN"] for i in ids}


class Served:
    def __init__(self, server, backend, token):
        self.server, self.backend, self.token = server, backend, token
        self.url = f"http://127.0.0.1:{server.server_port}"

    def __call__(self, verb: str, **req):
        """A server verb as the operator."""
        return post(self.url, self.token, verb, **req)

    def start(self, team=None, mission=None, seed=True, **req) -> Run:
        """A new run of `mission` (a folder with BRIEF.md, by default one made here), started and seeded."""
        team = team or [{"harness": "claude-code"}]
        if mission is None:
            mission = Path(self.server.data).parent / f"mission-{secrets.token_hex(2)}"
            mission.mkdir()
            (mission / "BRIEF.md").write_text("# Nothing\n")
        data = self("start", mission=mission.name, team=team, **{"me": "vivek", **req})
        assert "run" in data, data
        run = Run(self, data)
        if seed:
            make_seed(mission, run.url, self.token)
        return run


@pytest.fixture
def serve(tmp_path, monkeypatch):
    """serve() starts the one server, stopped at the end of the test."""
    for k in [k for k in os.environ if k.endswith("_API_KEY") or k.startswith("SWARMORY_")]:
        monkeypatch.delenv(k)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(k, "sk-dummy-" + k.lower())
    token = secrets.token_hex(16)
    monkeypatch.setenv("SWARMORY_OPERATOR_TOKEN", token)
    from swarmory.control_plane.server import Server
    started = []

    def start():
        server = Server(tmp_path / "data", "127.0.0.1", 0, Stub(), tick=0.2)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return Served(server, server.backend, token)

    yield start
    for server in started:
        server.shutdown()
        server.close()


@pytest.fixture
def fake_agents(tmp_path, monkeypatch):
    """Agents of the fake harness (tests/fake_harness.py): in HARNESSES, through tests/site, in every process a test
    starts, and its CLI on PATH. Its model calls go to a stand-in for OpenAI, which answers each with some usage."""
    import fake_harness
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class OpenAI(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            body = json.dumps({"model": "gpt-6.1-sol", "usage": {"input_tokens": 10, "output_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), OpenAI)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    monkeypatch.setenv("SWARMORY_PROXY_OPENAI", f"http://127.0.0.1:{upstream.server_port}")
    bin_ = tmp_path / "fake-bin"
    bin_.mkdir()
    (bin_ / "fake-agent").write_text(fake_harness.CLI.format(python=sys.executable))
    (bin_ / "fake-agent").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("PYTHONPATH", f"{os.environ['PYTHONPATH']}{os.pathsep}{TESTS / 'site'}")
    yield [{"harness": "fake"}]
    upstream.shutdown()
    upstream.server_close()
