"""A run's report: one headless coding-agent session, run as you, with your shell's environment and login, reads the
run's records and writes report.html beside them (report.md tells it how).

    uv run tools/report.py RUN_FOLDER [--harness HARNESS] [--model MODEL]

RUN_FOLDER is a run's export, as `swarmory export RUN` unpacks it. records() lays it out as the reporter reads it, in
RUN_FOLDER/records: every file read-only but main/, a clone of the team's repo with no remote."""
import argparse
import json
import shutil
import string
import subprocess
import sys
from pathlib import Path

from swarmory import harness
from swarmory.cli import git


def records(run: Path) -> Path:
    """The run's records, fresh, in run/records: the brief from main's first commit, run.json with the team and no
    digest, the log, ledger and shared files under server/, each agent's sessions and agent.log under agents/,
    and main/, a clone of the team's main."""
    out = run / "records"
    shutil.rmtree(out, ignore_errors=True)
    (out / "server").mkdir(parents=True)
    git(run, "clone", "-q", "--branch", "main", "work.bundle", str(out / "main"))
    git(out / "main", "remote", "remove", "origin")
    for name in ("log.jsonl", "ledger.jsonl"):
        if (run / name).is_file():
            shutil.copy(run / name, out / "server")
    if (run / "files").is_dir():
        shutil.copytree(run / "files", out / "server" / "files")
    for member in sorted((run / "members").glob("*")):
        dest = out / "agents" / member.name
        if (member / "sessions").is_dir():
            shutil.copytree(member / "sessions", dest / "sessions")
        if (member / "agent.log").is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy(member / "agent.log", dest)
    seed = git(out / "main", "rev-list", "--max-parents=0", "HEAD").split()[-1]
    (out / "BRIEF.md").write_text(git(out / "main", "show", f"{seed}:BRIEF.md"))
    config = json.loads((run / "config.json").read_text())
    team = [m for m in config["members"] if m["kind"] == "agent"]
    keep = ("run", "mission", "operator", "deadline", "created", "subscription", "budget")
    (out / "run.json").write_text(json.dumps({**{k: config[k] for k in keep if k in config}, "team": team}, indent=1))
    for f in out.rglob("*"):
        if f.is_file() and f.relative_to(out).parts[0] != "main":
            f.chmod(0o444)
    return out


def report(run: Path, name: str, model) -> Path:
    """Has one session of the harness `name` write run/report.html from the run's records. Its output goes to
    run/report.log."""
    folder, page = records(run), run / "report.html"
    page.unlink(missing_ok=True)
    prompt = string.Template(Path(__file__).with_name("report.md").read_text()).substitute(
        run=run.name, records=folder, report=page)
    cmd = harness.named(name).once(prompt, model, str(folder))
    if not shutil.which(cmd[0]):
        sys.exit(f"There is no `{cmd[0]}` on your PATH.")
    print(f"Writing the report with {name} from {folder} (its output: {run / 'report.log'})...", flush=True)
    with open(run / "report.log", "wb") as log:
        code = subprocess.run(cmd, cwd=folder, stdin=subprocess.DEVNULL, stdout=log, stderr=log).returncode
    if not page.is_file():
        sys.exit(f"{name} exited with {code} and wrote no report; see {run / 'report.log'}.")
    return page


def main():
    names = [h.name for h in harness.HARNESSES]
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run", type=Path, help="a run's export, unpacked")
    p.add_argument("--harness", choices=names, default=names[0], help=f"the coding agent (default {names[0]})")
    p.add_argument("--model", help="its model (default: the harness's own)")
    a = p.parse_args()
    print(report(a.run.resolve(), a.harness, a.model))


if __name__ == "__main__":
    main()
