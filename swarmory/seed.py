"""The seed: a mission folder pushed to a new run as the first commit on main, which is what starts its agents. The
client does this after `start`, from the machine that has the mission, with the operator token. Symlinks are copied as
files, and what withheld() names never reaches the team: secrets, links out of the mission folder, a .git, and
what the mission's own .gitignore ignores."""
import fnmatch
import os
import shutil
import sys
import tempfile
from pathlib import Path

from swarmory.cli import git

PRIVATE = (".env", ".env.*", "*.pem", "*.key")  # secrets a mission folder may hold, never given to the team


def plain_env(**extra) -> dict:
    """An environment without the shell's keys."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "SHELL", "TMPDIR")
    return {**{k: os.environ[k] for k in keep if k in os.environ}, **extra}


def private(mission: Path, rel: Path) -> bool:
    """rel, in the mission folder, is a secret by its name or its link's target's (PRIVATE), or links out of it. The
    skills `npx skills add` installs in a mission stay in it: .agents/skills, and .claude/skills links to that."""
    real = (mission / rel).resolve()
    return (any(fnmatch.fnmatch(n, g) for n in (rel.name, real.name) for g in PRIVATE)
            or not real.is_relative_to(mission))


def withheld(mission: Path, rel: Path) -> bool:
    """rel, in the mission folder, never reaches the team: a .git, a __pycache__, or what private() names."""
    return bool({".git", "__pycache__"} & set(rel.parts)) or private(mission, rel)


def make_seed(mission, url: str, token: str):
    """Pushes the mission folder to the run at `url` (http://HOST/r/RUN) as the first commit on main."""
    mission, gone = Path(mission).resolve(), []

    def skip(d, names):
        rels = {n: Path(d, n).relative_to(mission) for n in names}
        gone.extend(str(r) for r in rels.values() if private(mission, r))
        dangling = lambda n: Path(d, n).is_symlink() and not Path(d, n).exists()  # noqa: E731
        return [n for n, r in rels.items() if withheld(mission, r) or dangling(n)]

    with tempfile.TemporaryDirectory() as tmp:
        seed = Path(tmp) / "seed"
        shutil.copytree(mission, seed, symlinks=False, ignore=skip)
        if gone:
            print("Left out of the team's repo (secrets, or links out of the mission folder):", ", ".join(sorted(gone)),
                  file=sys.stderr)
        title = ((mission / "BRIEF.md").read_text().splitlines() or [""])[0].lstrip("# ").strip() or mission.name
        env = plain_env(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")  # not your own config: signing, hooks
        git(seed, "init", "-q", "-b", "main", env=env)
        git(seed, "-c", f"core.excludesFile={os.devnull}", "add", "-A", env=env)  # the mission's .gitignore, not yours
        git(seed, "-c", "user.name=swarmory", "-c", "user.email=swarmory@localhost", "commit", "-q", "-m", title,
            env=env)
        git(seed, "push", "-q", url + "/git/work.git", "main", token=token, env=env)
