"""Per-member push patterns in the pre-receive hook (fed by the server's environment), per-member fetch through
uploadpack.hideRefs, and both again with a gzip-encoded request body."""
import os
import subprocess, sys
sys.path.insert(0, os.path.join(os.environ.get("SWARMORY_REPO", "."), "tests")); sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audit-repros"))
from conftest import serve, together  # noqa
from test_gzip_push import gzipper

def test_refs(serve, tmp_path):
    served = serve()
    run = served.start(team=[{"harness": "claude-code", "name": "r1", "tags": ["squad:red"]},
                             {"harness": "claude-code", "name": "b1", "tags": ["squad:blue"]}, {"harness": "claude-code", "name": "free"}])
    tok = run.workers("r1", "b1", "free")
    proxy, _ = gzipper(served.url)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "NO_PROXY": "*", "GIT_TERMINAL_PROMPT": "0"}
    def g(who, *a, gz=False):
        base = f"127.0.0.1:{proxy.server_port}" if gz else served.url[7:]
        w = tmp_path / who
        if not w.exists():
            subprocess.run(["git", "clone", "-q", f"http://x:{tok[who]}@{served.url[7:]}/r/{run.id}/git/work.git", str(w)], env=env, check=True)
        subprocess.run(["git", "-C", str(w), "remote", "set-url", "origin", f"http://x:{tok[who]}@{base}/r/{run.id}/git/work.git"], env=env)
        return subprocess.run(["git", "-C", str(w), "-c", "http.proxy=", "-c", "user.name=" + who, "-c", "user.email=a@b", *a], capture_output=True, text=True, env=env)
    def commit(who, name):
        (tmp_path / who / name).write_text(name); g(who, "add", "."); g(who, "commit", "-qm", name)
    [g(w, "status") for w in tok]
    commit("r1", "red.txt"); commit("b1", "blue.txt"); commit("free", "free.txt")
    out = lambda r: (r.returncode, (r.stderr.strip().splitlines() or [""])[0][-110:])
    def check(label, r, code):
        print(f"{label:32}", out(r))
        assert r.returncode == code, (label, out(r))
    check("red -> squad/red", g("r1", "push", "origin", "HEAD:refs/heads/squad/red"), 0)
    check("red -> main", g("r1", "push", "origin", "HEAD:refs/heads/main"), 1)
    check("blue -> squad/red", g("b1", "push", "origin", "HEAD:refs/heads/squad/red"), 1)
    check("blue -> squad/red, gzip body", g("b1", "push", "origin", "HEAD:refs/heads/squad/red", gz=True), 1)
    check("blue -> squad/blue, gzip body", g("b1", "push", "origin", "HEAD:refs/heads/squad/blue", gz=True), 0)
    check("untagged -> main", g("free", "push", "origin", "HEAD:refs/heads/main"), 0)
    check("untagged -> other", g("free", "push", "origin", "HEAD:refs/heads/x"), 1)
    ls = lambda who: sorted(l.split("\t")[1] for l in g(who, "ls-remote", "origin").stdout.splitlines())
    assert ls("r1") == ["HEAD", "refs/heads/main", "refs/heads/squad/red"]
    assert ls("b1") == ["HEAD", "refs/heads/main", "refs/heads/squad/blue"]
    assert ls("free") == ["HEAD", "refs/heads/main", "refs/heads/squad/blue", "refs/heads/squad/red"]
    red = subprocess.run(["git", "-C", str(tmp_path / "r1"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    check("blue fetches red's commit by id", g("b1", "fetch", "origin", red), 128)
    check("blue fetches squad/red by name", g("b1", "fetch", "origin", "refs/heads/squad/red"), 128)
    check("red fetches squad/red", g("r1", "fetch", "origin", "refs/heads/squad/red"), 0)
