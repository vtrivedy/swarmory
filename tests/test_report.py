"""tools/report.py on a synthetic run's export, with a fake claude and codex that record how they were started and
write report.html: the records folder holds the brief as given, the team and the log, main is a clone with no remote,
the records are read-only, the harness gets tools/report.md with the paths filled in, and the command prints the
page's path. No model is called."""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FAKE = """#!{python}
import json, pathlib, sys
here = pathlib.Path.cwd()
try:
    (here / "server" / "log.jsonl").write_text("")
    wrote = True
except PermissionError:
    wrote = False
pathlib.Path({out!r}, "seen.json").write_text(json.dumps({{"argv": sys.argv, "cwd": str(here), "wrote_log": wrote}}))
(here.parent / "report.html").write_text("<!doctype html><title>report</title>")
"""


def sh(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def synthetic_export(tmp: Path) -> Path:
    """A run's export, unpacked, as GET /r/RUN/export writes it: no token, no digest, and no agent's home."""
    rdir = tmp / "exports" / "demo-1104-0000-abcd"
    rdir.mkdir(parents=True)
    config = {"run": rdir.name, "mission": "demo", "deadline": 2, "created": 1, "operator": "vivek",
              "members": [{"id": "ada", "name": "Ada", "harness": "claude-code", "model": None, "kind": "agent"},
                          {"id": "vivek", "name": "Vivek", "kind": "human", "operator": True}]}
    (rdir / "config.json").write_text(json.dumps(config))
    events = [{"seq": 1, "at": 1, "type": "message", "id": "m1", "conv": "general", "author": "ada", "text": "hi"},
              {"seq": 2, "at": 2, "type": "push", "by": "ada", "ref": "refs/heads/main", "old": "a", "new": "b",
               "ok": True}]
    (rdir / "log.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    seed = tmp / "seed"
    seed.mkdir()
    commit = ("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q")
    sh("git", "init", "-q", "-b", "main", cwd=seed)
    (seed / "BRIEF.md").write_text("# The brief as given\n")
    sh("git", "add", "-A", cwd=seed)
    sh(*commit, "-m", "seed", cwd=seed)
    (seed / "BRIEF.md").write_text("# The brief, edited by the team\n")
    sh(*commit, "-am", "edit", cwd=seed)
    sh("git", "bundle", "create", str(rdir / "work.bundle"), "--all", cwd=seed)
    return rdir


def report(tmp: Path, *args) -> subprocess.CompletedProcess:
    """tools/report.py, with only tmp/bin's fake harnesses on PATH."""
    env = {"PATH": f"{tmp / 'bin'}:{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(tmp),
           "PYTHONPATH": os.environ["PYTHONPATH"]}
    return subprocess.run([sys.executable, str(REPO / "tools" / "report.py"), *args], env=env, cwd=REPO,
                          capture_output=True, text=True, timeout=60)


def fake(tmp: Path, name: str, script: str):
    (tmp / "bin").mkdir(exist_ok=True)
    (tmp / "bin" / name).write_text(script)
    (tmp / "bin" / name).chmod(0o755)


def test_report_gathers_the_records_and_runs_the_harness_on_them(tmp_path):
    rdir = synthetic_export(tmp_path)
    for harness, cli in (("claude-code", "claude"), ("codex", "codex")):
        fake(tmp_path, cli, FAKE.format(python=sys.executable, out=str(tmp_path)))
        r = report(tmp_path, str(rdir), "--harness", harness, "--model", "m-1")
        assert r.returncode == 0, r.stdout + r.stderr
        page = rdir / "report.html"
        assert r.stdout.strip().splitlines()[-1] == str(page) and page.is_file()

        records = rdir / "records"
        assert (records / "BRIEF.md").read_text() == "# The brief as given\n"
        assert json.loads((records / "run.json").read_text())["team"][0]["harness"] == "claude-code"
        assert (records / "server" / "log.jsonl").read_text() == (rdir / "log.jsonl").read_text()
        main = records / "main"
        assert (main / "BRIEF.md").read_text() == "# The brief, edited by the team\n"
        assert not subprocess.run(["git", "remote"], cwd=main, capture_output=True, text=True).stdout.strip()

        seen = json.loads((tmp_path / "seen.json").read_text())
        assert Path(seen["cwd"]).resolve() == records.resolve()
        assert not seen["wrote_log"], "the records are read-only to the reporter"
        prompt = seen["argv"][-1] if harness == "codex" else seen["argv"][seen["argv"].index("-p") + 1]
        assert str(records) in prompt and str(page) in prompt and "$" not in prompt
        assert "m-1" in seen["argv"]
        if harness == "claude-code":
            skip = json.loads(seen["argv"][seen["argv"].index("--settings") + 1])["claudeMdExcludes"]
            assert str(tmp_path.resolve() / "CLAUDE.md") in skip, "no CLAUDE.md from above the records"
