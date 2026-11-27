"""The swarmory server: one HTTP server that owns every run and supervises their workers through a backend.

    swarmory serve [--data DIR] [--host 127.0.0.1] [--port N] [--backend local|modal] [--url URL]

GET /health and POST /api/<verb> (start, runs, status, stop) are the server's. Each run is under
/r/RUN: its verbs (/api/<verb>), its git remote (/git/work.git/..., which logs each push), its model gateway
(/llm/anthropic/..., /llm/openai/..., /llm/gemini/...), shared files (/files), agents' traces (/trace), a job's input
(/job/input), the viewer (/view, which polls /feed, /commits, /commit and /trace) and the run's records (/export).
verbs.py holds the verbs, runs.py the runs, supervisor.py the loop that keeps their workers.

A token comes as a Bearer, an x-api-key, an x-goog-api-key or git's Basic-auth password. The operator token is the
server's, from SWARMORY_OPERATOR_TOKEN in its environment. Every route and every verb names one action, and policy.py's
one table says which kinds of token (operator, member, job, spend, view) may do it. The model keys are in this
process's environment (proxy.py) and are never passed on, not even to git or a worker."""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from swarmory import PORT, POST_BUFFER, TRACE_MOST

from .jobs import TOKENS, git
from .litellm import LiteLLM, install
from .policy import OWN, TABLE, Denied, Principal, authorize, sha
from .proxy import KEY_HEADER, length
from .runs import SESSION, Runs
from .supervisor import RESTART, TICK, Supervisor
from .team import NotFound
from .verbs import VERBS

PAGE = Path(__file__).with_name("view.html").read_bytes()  # read once, so the page served is the page hashed
SCRIPTS = " ".join("'sha256-%s'" % base64.b64encode(hashlib.sha256(js).digest()).decode()
                   for js in re.findall(rb"<script[^>]*>(.*?)</script>", PAGE, re.S))
PAGE_HEADERS = [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store"),
                # a link on the page tells no other site the server's address
                ("Referrer-Policy", "no-referrer"),
                ("Content-Security-Policy", f"default-src 'none'; script-src {SCRIPTS}; style-src 'unsafe-inline'; "
                                            "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; "
                                            "frame-ancestors 'none'")]
FILE_MOST = 512 << 20  # bytes of one uploaded file, so one member cannot fill the server's disk in a request
VIEWS = {"/feed": "feed", "/commits": "commits", "/commit": "commit", "/trace": "trace"}  # what a view token opens


def pkt_lines(data: bytes) -> list:
    """git's pkt-lines at the start of data, up to a flush-pkt or anything that is not a pkt-line."""
    out, i = [], 0
    while re.fullmatch(rb"[0-9a-f]{4}", data[i:i + 4]) and int(data[i:i + 4], 16) >= 4:
        n = int(data[i:i + 4], 16)
        out.append(data[i + 4:i + n])
        i += n
    return out


def pushes(request: bytes, response: bytes) -> list:
    """The ref updates a receive-pack request asked for, each ok as receive-pack's report-status says (git always
    asks for one), with its reason when it refused."""
    asked = [line.partition(b"\0") for line in pkt_lines(request)]
    report = pkt_lines(response)
    if asked and {b"side-band", b"side-band-64k"} & set(asked[0][2].split()):
        report = pkt_lines(b"".join(p[1:] for p in report if p[:1] == b"\x01"))
    said = {}  # ref -> None if accepted, else the reason
    for line in report:
        word, _, rest = line.decode(errors="replace").rstrip("\n").partition(" ")
        ref, _, reason = rest.partition(" ")
        if word in ("ok", "ng"):
            said[ref] = None if word == "ok" else reason
    out = []
    for cmd in (c.decode(errors="replace").split() for c, _, _ in asked):
        if len(cmd) != 3:  # a shallow line
            continue
        old, new, ref = cmd
        reason = said.get(ref, "no report")
        out.append({"ref": ref, "old": old, "new": new, "ok": reason is None, **({"reason": reason} if reason else {})})
    return out


class Handler(BaseHTTPRequestHandler):
    timeout = 120  # on each socket read and write, so a stalled client can't hold a thread; sleeps wait in here

    def log_request(self, code="-", size="-"):
        """Only the server's own failures are logged: git's first request gets a 401, and a trace sent again a 409."""
        if int(code) >= 500:
            super().log_request(code, size)

    def answer(self, code: int, body, headers=()):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
            headers = [("Content-Type", "application/json"), *headers]
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def token(self) -> str:
        """The token this request carries: Bearer, x-api-key, x-goog-api-key (Gemini's), or a Basic-auth password
        (git's)."""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                return base64.b64decode(auth[6:]).decode().partition(":")[2].strip()
            except ValueError:
                return ""
        return (self.headers.get("x-api-key") or self.headers.get("x-goog-api-key")
                or (auth[7:] if auth.startswith("Bearer ") else "")).strip()

    def principal(self):
        """Whose token came with this request (policy.Principal), or None: an empty or unknown token is no one's. The
        operator is the operator member of each run. A stopped worker's last trace upload has a minute's grace."""
        token = self.token()
        if not token:
            return None
        if hmac.compare_digest(sha(token), self.server.operator):
            return Principal("operator", self.run and self.run.operator)
        grace = 60 if self.path.partition("?")[0].endswith("/trace") and self.command == "POST" else 0
        return self.run.principal(token, grace) if self.run else None

    def allow(self, action: str, said=None, **scope):
        """The request's Principal if policy lets it do `action`. Else None, after answering 401 to no good token or
        403 to one that may not: with `said`, or the reason."""
        who = self.principal()
        try:
            return authorize(who, action, **scope)
        except Denied as e:
            self.answer(403 if who else 401, said if said is not None else
                        {"error": str(e) if who else "unknown token: run `swarmory login URL TOKEN`"})

    def gone(self) -> bool:
        """The client has hung up (a killed agent): its socket reads as closed."""
        poll = select.poll()  # not select(), which fails on a socket numbered past 1023
        poll.register(self.connection, select.POLLIN)
        try:
            return bool(poll.poll(0)) and not self.connection.recv(1, socket.MSG_PEEK)
        except OSError:
            return True

    def route(self):
        path, _, query = self.path.partition("?")
        self.run, self.query = None, urllib.parse.parse_qs(query)
        if path == "/health":
            return self.answer(200, {"ok": True})
        if path.startswith("/api/"):
            return self.verb(path[5:], "server")
        m = re.fullmatch(r"/r/([\w-]+)(/.*)", path)
        self.run = m and self.server.runs.get(m[1])
        if not self.run:
            return self.answer(404, {"error": "no such run: the server serves /health, /api and /r/RUN/..."})
        path = m[2]
        if path == "/view":
            return self.answer(200, PAGE, PAGE_HEADERS)
        if path.startswith("/files"):
            return self.files(path)
        if path == "/trace" and self.command == "POST":
            return self.upload_trace()
        if path in VIEWS:
            if not self.allow("view", {"error": "open the watch link that `swarmory status RUN` prints"}):
                return
            return self.respond(path, lambda: getattr(self.run, VIEWS[path])(self.query))
        if path.startswith("/api/"):
            return self.verb(path[5:], "run")
        if path.startswith("/git/"):
            return self.git(path[len("/git"):], query)
        if path == "/job/input":
            return self.job_input()
        if path.startswith("/llm/"):
            return self.llm(path[len("/llm/"):] + (f"?{query}" if query else ""))
        if path == "/export":
            return self.allow("server") and self.export()
        self.answer(404, {"error": "a run serves /api, /git, /llm, /files, /trace, /job/input, /view, /feed, /commits, "
                                   "/commit and /export"})

    do_GET = do_POST = route

    def respond(self, what: str, fn):
        """fn()'s answer, or its error: 400 a bad request, 404 an unknown name, 500 a bug or a failed git."""
        try:
            out = fn()
        except ValueError as e:
            return self.answer(400, {"error": str(e)})
        except NotFound as e:
            return self.answer(404, {"error": str(e)})
        except Exception as e:  # say so, rather than drop the connection
            self.log_error("%s failed: %r", what, e)
            return self.answer(500, {"error": f"the server failed: {e!r}"})
        self.answer(200, out)

    def verb(self, name: str, scope: str):
        """POST a verb of verbs.py, the server's or the run's: {"data": ..., "text": ...}."""
        n = length(self.headers)
        if n is None or n > 1 << 20:
            return self.answer(413, {"error": "send a JSON body of at most 1 MB, with a Content-Length"})
        raw, verb = self.rfile.read(n), VERBS.get(name)
        if self.command != "POST" or not verb or verb.scope != scope:
            if self.allow("server" if scope == "server" else "team"):
                names = ", ".join(v.name for v in VERBS.values() if v.scope == scope)
                self.answer(404, {"error": f"no verb {name}: POST one of {names}"})
            return
        bad = None  # said once the token is known to be good
        try:
            req = json.loads(raw or b"{}")
            if not isinstance(req, dict):
                bad = "the body must be a JSON object"
        except RecursionError:
            bad = "the body nests too deep"
        except ValueError as e:
            bad = str(e)
        req = {} if bad else req
        action, owner = verb.action, {}
        if OWN in TABLE[action].values() and any(p.name == "id" for p in verb.params):  # about one job: whose it is
            owner = {"owner": self.run.jobs.owner(req), "job": req.get("id")}
            if owner["owner"] is None:
                action = "job.read"  # no such job: whoever may look is told so
        who = self.allow(action, **owner)
        if not who:
            return

        def call():
            if bad:
                raise ValueError(bad)
            data = verb.call(self.server, req, self.token()) if scope == "server" else \
                verb.call(self.run, who, req, self.gone)
            return {"data": data, "text": verb.text(data, req)}
        self.respond(f"{scope} verb {name}", call)

    def git(self, path: str, query: str):
        if not path.startswith("/work.git/") or ".." in path:
            return self.answer(404, b"Only /git/work.git lives here.\n")
        who = self.principal()
        try:
            who = authorize(who, "git").member
        except Denied:  # git asks for a password on a 401, and gives up on a 403
            return self.answer(403 if who else 401, b"", [("WWW-Authenticate", 'Basic realm="swarmory"')])
        n = length(self.headers)
        if n is None:
            return self.answer(411, f"Send a Content-Length: git config http.postBuffer {POST_BUFFER}\n".encode())
        if n > 1 << 29:
            return self.answer(413, b"Push at most 512 MB at once.\n")
        body = self.rfile.read(n)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_PROJECT_ROOT": str(self.run.dir),
               "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": path, "QUERY_STRING": query, "REQUEST_METHOD": self.command,
               "CONTENT_TYPE": self.headers.get("Content-Type", ""), "CONTENT_LENGTH": str(len(body)),
               "REMOTE_USER": who, "REMOTE_ADDR": self.client_address[0]}
        push, fetch = self.run.refs(who)
        env["SWARMORY_PUSH"] = " ".join(push)
        hide = [] if fetch is None else ["refs/", *("!" + r for r in fetch)]
        env["GIT_CONFIG_COUNT"] = str(len(hide))
        for i, h in enumerate(hide):
            env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = "uploadpack.hideRefs", h
        if self.headers.get("Content-Encoding"):
            env["HTTP_CONTENT_ENCODING"] = self.headers["Content-Encoding"]
        if self.headers.get("Git-Protocol") and fetch is None:  # v2 serves any object by id, hidden ref or not
            env["GIT_PROTOCOL"] = self.headers["Git-Protocol"]
        with tempfile.TemporaryFile() as sent:  # a file, so git can answer while it still reads a large request
            sent.write(body)
            sent.seek(0)
            with subprocess.Popen(["git", "http-backend"], stdin=sent, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL) as p:
                code, headers = 200, []
                while (line := p.stdout.readline().decode("latin-1").strip()):
                    key, _, value = line.partition(":")
                    if key.lower() == "status":
                        code = int(value.split()[0])
                    else:
                        headers.append((key.strip(), value.strip()))
                if path.endswith("/git-receive-pack"):  # logged before the answer, so a pusher that reads the log
                    rest = p.stdout.read()              # sees it; its report is a few lines
                    self.run.team.pushed(who, pushes(body, rest))
                    self.run.tip = (0.0, None)  # main may have moved: the next look reads it again
                    return self.answer(code, rest, headers)
                # a clone's pack goes out as git writes it, never whole in memory: no length, so the close ends it
                self.close_connection = True
                self.send_response(code)
                for k, v in headers:
                    if k.lower() != "content-length":
                        self.send_header(k, v)
                self.end_headers()
                shutil.copyfileobj(p.stdout, self.wfile)

    def files(self, path: str):
        """POST files?name=NAME uploads a member's file and answers its id. GET files/<id> gives it back to the
        viewer, or to a member who uploaded it or may read a message it was sent in."""
        store, team = self.run.dir / "files", self.run.team
        if self.command == "GET":
            fid, who = path[len("/files/"):], self.allow("file.get")
            if not who:
                return
            if (not re.fullmatch(r"f\d+", fid) or not (store / fid).is_file()
                    or who.member and not team.may_get(who.member, fid)):
                return self.answer(404, {"error": f"there is no file {fid} you can read"})
            name = team.files[fid]["name"] if fid in team.files else fid
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", "attachment; filename=\"%s\"" % re.sub(r'["\\\r\n]', "_", name))
            self.send_header("Content-Length", str((store / fid).stat().st_size))
            self.end_headers()
            with open(store / fid, "rb") as f:
                return shutil.copyfileobj(f, self.wfile)
        who, n = self.allow("file.upload"), length(self.headers)
        if not who:
            return
        who = who.member  # a job's artifacts are its submitter's file
        if path != "/files" or n is None:
            return self.answer(400, {"error": "POST /files?name=NAME with the file as the body, and a Content-Length"})
        name = os.path.basename(self.query.get("name", [""])[0])
        if not 0 < len(name) <= 200 or "\0" in name:
            return self.answer(400, {"error": "name the file: ?name= takes a file name of 1 to 200 characters"})
        if n > FILE_MOST:
            return self.answer(413, {"error": f"a file is at most {FILE_MOST >> 20} MB"})
        store.mkdir(exist_ok=True)
        tmp = store / f".up-{secrets.token_hex(8)}"
        try:
            with open(tmp, "wb") as f:
                left = n
                while left:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        return self.answer(400, {"error": "the upload ended early"})
                    f.write(chunk)
                    left -= len(chunk)
            self.respond("upload", lambda: team.upload(who, name, n, lambda fid: tmp.rename(store / fid)))
        finally:
            tmp.unlink(missing_ok=True)

    def upload_trace(self):
        """POST trace?name=session-NNN.jsonl&at=OFFSET (or name=agent.log, the worker's own log): bytes of a member's
        file, from OFFSET, which must be the server's copy's size; else 409 with that size, for `swarmory trace` to send
        from there. Answers the new size."""
        who, n = self.allow("trace.upload"), length(self.headers)
        if not who:
            return
        who = who.member
        name, at = self.query.get("name", [""])[0], self.query.get("at", [""])[0]
        if not (SESSION.fullmatch(name) or name == "agent.log") or not at.isdigit() or n is None or n > TRACE_MOST:
            self.rfile.read(min(n or 0, TRACE_MOST))
            return self.answer(400, {"error": f"POST /trace?name=session-NNN.jsonl (or agent.log)&at=OFFSET with at "
                                              f"most {TRACE_MOST} bytes and a Content-Length"})
        body = self.rfile.read(n)
        if len(body) < n:
            return self.answer(400, {"error": "the upload ended early"})
        kept, size = self.run.keep_trace(who, name, int(at), body)
        self.answer(200 if kept else 409, {"size": size})

    def llm(self, rest: str):
        provider, _, rest = rest.partition("/")
        if provider not in KEY_HEADER:
            return self.answer(404, {"error": "the model gateway serves /llm/anthropic/..., /llm/openai/... and "
                                              "/llm/gemini/..."})
        who, job = self.principal(), None
        try:
            authorize(who, "llm")
        except Denied:
            who = None  # the gateway refuses, in the provider's own error shape
        if who and who.kind == "spend":  # a job's trial: billed to the job, up to its limits
            job = {"id": who.job, "budget": self.run.jobs.jobs[who.job]["budget"], "tokens": TOKENS}
        self.run.proxy.handle(self, provider, rest, who and who.member, job)

    def job_input(self):
        """GET job/input?id=JOB[&part=harness]: the folder the job runs on, or its harness (its agent's folder), as a
        tar of the tree the server recorded for it. Only that job's own token opens it."""
        jid, part = self.query.get("id", [""])[0], self.query.get("part", ["task"])[0]
        if not self.allow("job.input", job=jid):
            return
        job = self.run.jobs.jobs[jid]
        tree = {"task": job["tree"], "harness": job["harness"]}.get(part)
        if not tree:
            return self.answer(404, {"error": f"job {jid} has no {part} folder"})
        with tempfile.TemporaryFile() as tar:  # whole first, so a failed git is a 500 and not half an archive
            if git(self.run.repo, "archive", "--format=tar", tree, into=tar, check=False) is None:
                return self.answer(500, {"error": "git archive failed"})
            self.send(tar, "application/x-tar")

    def export(self):
        """GET export: the run's records as a tar.gz (Run.export), for the operator."""
        with tempfile.TemporaryFile() as tar:
            self.run.export(tar)
            self.send(tar, "application/gzip",
                      [("Content-Disposition", f'attachment; filename="{self.run.id}.tar.gz"')])

    def send(self, f, kind: str, headers=()):
        """The whole of the temporary file f, as the answer."""
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(f.tell()))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        f.seek(0)
        shutil.copyfileobj(f, self.wfile)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, data: Path, host: str, port: int, backend, url: str = None, tick: float = TICK,
                 restart: float = RESTART):
        """Serves every run in `data`, and starts the loop that keeps their workers. `url` is where workers and clients
        reach this server (default http://HOST:PORT). The operator's token is SWARMORY_OPERATOR_TOKEN."""
        super().__init__((host, port), Handler)
        self.data, self.backend = Path(data), backend
        self.data.mkdir(mode=0o700, parents=True, exist_ok=True)  # agents' homes
        here = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        self.url = (url or f"http://{here}:{self.server_port}").rstrip("/")
        self.operator = sha(os.environ["SWARMORY_OPERATOR_TOKEN"])
        self.litellm = LiteLLM(self.data, os.environ)
        self.runs = Runs(self.data, self.litellm, self.url)
        self.supervisor = Supervisor(self.runs, backend, self.url, tick, restart)
        threading.Thread(target=self.supervisor.loop, daemon=True).start()

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, socket.timeout)):  # a client that hung up or stalled
            super().handle_error(request, client_address)

    def close(self):
        """Stops serving and supervising. The workers go on: a server started again on this data finds them."""
        self.supervisor.done.set()
        with self.supervisor.lock:  # a tick under way finishes first
            pass
        self.server_close()
        self.litellm.stop()


def main(argv=None):
    p = argparse.ArgumentParser(prog="swarmory serve", description="Runs the swarmory server, which holds every run.")
    p.add_argument("--data", default=str(Path.home() / ".swarmory" / "data"),
                   help="where runs are kept (~/.swarmory/data)")
    p.add_argument("--host", default="127.0.0.1", help="the address to listen on (127.0.0.1)")
    p.add_argument("--port", type=int, default=PORT, help=f"the port to listen on ({PORT})")
    p.add_argument("--backend", choices=("local", "modal"), default="local", help="where workers run (local)")
    p.add_argument("--url", help="the address workers and clients reach the server at (http://HOST:PORT)")
    a = p.parse_args(argv)
    if not os.environ.get("SWARMORY_OPERATOR_TOKEN"):
        sys.exit("Set SWARMORY_OPERATOR_TOKEN to the operator's token: a value you keep, like `openssl rand -hex 16`.")
    data = Path(a.data).expanduser().resolve()
    if not os.environ.get("SWARMORY_PROXY_LITELLM"):
        os.environ["SWARMORY_PROXY_LITELLM"] = install()
    if a.backend == "modal":  # swarmory/backends/modal.py, the only file that imports modal
        from swarmory.backends.modal import Modal
        backend = Modal()
    else:
        from swarmory.backends.local import Local
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        backend = Local(data)
    server = Server(data, a.host, a.port, backend, a.url)
    print(f"swarmory serves {len(server.runs.configs())} runs at {server.url}, from {data}", flush=True)
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # `kill PID` closes the server as ctrl-C does
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


if __name__ == "__main__":
    main()
