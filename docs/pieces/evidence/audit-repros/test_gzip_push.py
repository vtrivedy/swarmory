import os
"""A push whose request body is gzip-encoded (git http-backend inflates it; server.py L259 passes the header on).
Shows: the ref moves, server.pushes() parses nothing from the raw body, and no push event is logged."""
import gzip, json, subprocess, sys, threading, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, os.path.join(os.environ.get("SWARMORY_REPO", "."), "tests"))
from conftest import serve, together, OPENER  # noqa
from swarmory.control_plane.server import pushes

def gzipper(target):
    seen = []
    class H(BaseHTTPRequestHandler):
        def do(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            hdr = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "accept-encoding")}
            if body and self.path.endswith("git-receive-pack"):
                seen.append(body)
                body = gzip.compress(body); hdr["Content-Encoding"] = "gzip"
            req = urllib.request.Request(target + self.path, body, hdr, method=self.command)
            try:
                r = OPENER.open(req, timeout=60); code, out, rh = r.status, r.read(), r.headers
            except urllib.error.HTTPError as e:
                code, out, rh = e.code, e.read(), e.headers
            self.send_response(code)
            for k, v in rh.items():
                if k.lower() not in ("content-length", "transfer-encoding", "connection"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
        do_GET = do_POST = do
        def log_message(self, *_): pass
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, seen

def test_gzip_push(serve, tmp_path):
    served = serve(); run = served.start(team=[{"harness": "claude-code"}])
    tok = run.workers("ada")["ada"]
    proxy, seen = gzipper(served.url)
    via = f"http://x:{tok}@127.0.0.1:{proxy.server_port}/r/{run.id}/git/work.git"
    w = tmp_path / "w"
    g = lambda *a, **k: subprocess.run(["git", "-c", "http.proxy=", "-c", "user.name=ada", "-c", "user.email=a@b", *a],
                                       cwd=k.get("cwd", w), capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "NO_PROXY": "*"})
    print(g("clone", "-q", via, str(w), cwd=tmp_path).stderr)
    (w / "x.txt").write_text("x"); g("add", "."); g("commit", "-qm", "gz")
    log = Path(served.server.data) / "runs" / run.id / "log.jsonl"
    before = [json.loads(l) for l in log.read_text().splitlines() if '"push"' in l]
    r = g("push", "origin", "HEAD:main"); print("push main:", r.returncode, r.stderr.strip()[-200:])
    head = subprocess.run(["git", "-C", str(log.parent / "work.git"), "log", "-1", "--format=%s", "main"], capture_output=True, text=True).stdout.strip()
    after = [json.loads(l) for l in log.read_text().splitlines() if '"push"' in l]
    print("main head subject:", head, "| push events before/after:", len(before), len(after))
    print("pushes(raw body) ->", pushes(seen[-1], b""), "| pushes(gzip body) ->", pushes(gzip.compress(seen[-1]), b""))
    r = g("push", "origin", "HEAD:refs/heads/squad/red"); print("push other ref (pre-receive hook still there):", r.returncode, r.stderr.strip()[-160:])
    assert head == "gz" and len(after) == len(before)
