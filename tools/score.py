"""Grades a run's main with its mission's score.py, the hidden grader the team never saw. score.py runs the team's code,
on this machine.

    uv run tools/score.py MISSION RUN_FOLDER

MISSION is the mission folder, with score.py. RUN_FOLDER is the run's export, as `swarmory export RUN` unpacks it."""
import subprocess
import sys
import tempfile
from pathlib import Path

from swarmory.cli import git
from swarmory.seed import plain_env


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    mission, run = Path(sys.argv[1]), Path(sys.argv[2])
    if not (mission / "score.py").is_file():
        sys.exit(f"{mission} has no score.py.")
    if not (run / "work.bundle").is_file():
        sys.exit(f"{run} has no work.bundle: nothing was pushed to the run.")
    with tempfile.TemporaryDirectory() as clone:
        git(run, "clone", "-q", "--branch", "main", "work.bundle", clone)
        print("main at " + git(clone, "log", "--oneline", "-1").strip(), flush=True)
        sys.exit(subprocess.run(["python3", "score.py", clone], cwd=mission, env=plain_env()).returncode)


if __name__ == "__main__":
    main()
