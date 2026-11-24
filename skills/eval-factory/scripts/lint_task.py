#!/usr/bin/env python3
"""Check a Harbor task folder against eval-factory's isolation and grading rules, without building anything.

    python3 lint_task.py tasks/<task-name>

Prints one JSON line per check, {"check", "status": "ok" or "fail", "detail"}, and exits 1 if any check fails.
The last check runs the task's verifier on its stored reference case the way the template's test.sh does:
`python3 -I tests/verifier.py tests/cases/reference/output <verifier dir>`. Needs Python 3.11 or later.
"""
import json
import math
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT_USERS = {"root", "0"}
GRADING_LINE = re.compile(r"\b(python3?|verifier\.py|pytest|uv)\b")
WIPE_LINE = re.compile(r"\brm\b.*?/logs/verifier/reward\.(\*|json|txt)")


def final_user(dockerfile):
    users = re.findall(r"^\s*USER\s+(\S+)", dockerfile.read_text(), re.MULTILINE | re.IGNORECASE)
    return users[-1].split(":")[0] if users else None


def check_schema_version(task, toml):
    version = toml.get("schema_version")
    return version is not None, f"schema_version = {version!r}"


def check_verifier_separate(task, toml):
    mode = toml.get("verifier", {}).get("environment_mode", "shared (Harbor's default)")
    return mode == "separate", f"[verifier] environment_mode = {mode!r}"


def check_verifier_dockerfile(task, toml):
    present = (task / "tests/Dockerfile").is_file()
    missing = "tests/Dockerfile missing; separate mode builds the verifier's image from tests/"
    return present, "tests/Dockerfile present" if present else missing


def check_verifier_not_solver(task, toml):
    dockerfile = task / "tests/Dockerfile"
    agent = toml.get("agent", {}).get("user")
    user = final_user(dockerfile) if dockerfile.is_file() else None
    return not (agent and user == agent), f"tests/Dockerfile final USER = {user!r}, solver user = {agent!r}"


def check_agent_user(task, toml):
    user = toml.get("agent", {}).get("user")
    return bool(user) and str(user) not in ROOT_USERS, f"[agent] user = {user!r}"


def check_dockerfile_user(task, toml):
    dockerfile = task / "environment/Dockerfile"
    if not dockerfile.is_file():
        return False, "environment/Dockerfile missing"
    user = final_user(dockerfile)
    return user is not None and user not in ROOT_USERS, f"environment/Dockerfile final USER = {user!r}"


def check_solver_network_narrow(task, toml):
    environment = toml.get("environment", {})
    mode, hosts = environment.get("network_mode"), environment.get("allowed_hosts")
    if mode == "allowlist":
        ok = isinstance(hosts, list) and bool(hosts) and all(isinstance(h, str) and h.strip() for h in hosts)
        return ok, f"[environment] network_mode = 'allowlist', allowed_hosts = {hosts!r}"
    return mode == "no-network", f"[environment] network_mode = {mode!r}"


def copied_sources(dockerfile):
    """The source paths of every COPY or ADD from the build context."""
    text = re.sub(r"\\\n", " ", dockerfile.read_text())
    for line in text.splitlines():
        words = line.split()
        if len(words) < 2 or words[0].upper() not in ("COPY", "ADD"):
            continue
        if any(w.startswith("--from") for w in words[1:]):
            continue
        args = [w for w in words[1:] if not w.startswith("--")]
        if args and args[0].startswith("["):
            try:
                args = json.loads(" ".join(args))
            except ValueError:
                pass
        yield from (a for a in args[:-1] if not a.startswith("<<"))


def check_image_has_no_task_files(task, toml):
    dockerfile = task / "environment/Dockerfile"
    if not dockerfile.is_file():
        return False, "environment/Dockerfile missing"
    bad = [s for s in copied_sources(dockerfile)
           if s.strip("/") in (".", "") or {"tests", "solution"} & set(Path(s).parts)]
    return not bad, f"environment/Dockerfile copies {bad!r}" if bad else "no COPY or ADD of ., tests or solution"


def check_artifacts_declared(task, toml):
    artifacts = toml.get("artifacts")
    return isinstance(artifacts, list) and bool(artifacts), f"artifacts = {artifacts!r}"


def check_test_sh_wipes_reward(task, toml):
    script = task / "tests/test.sh"
    if not script.is_file():
        return False, "tests/test.sh missing"
    lines = [line.split("#")[0] for line in script.read_text().splitlines()[1:]]
    errexit = any(re.search(r"\bset\s+-\w*e", line) for line in lines)
    wiped = set()
    for line in lines:
        if WIPE_LINE.search(line):
            wiped |= {"json", "txt"} if "reward.*" in line else {s for s in ("json", "txt") if f"reward.{s}" in line}
        elif GRADING_LINE.search(line):
            break
    ok = errexit and wiped == {"json", "txt"}
    return ok, f"set -e: {errexit}; deletes before grading: {sorted('reward.' + s for s in wiped)}"


def check_reference_reward_numeric(task, toml):
    verifier, reference = task / "tests/verifier.py", task / "tests/cases/reference/output"
    if not verifier.is_file() or not reference.is_dir():
        return False, "needs tests/verifier.py and tests/cases/reference/output/"
    with tempfile.TemporaryDirectory() as out:
        run = subprocess.run([sys.executable, "-I", str(verifier), str(reference), out], cwd=out,
                             capture_output=True, text=True, timeout=120, check=False)
        reward_file = Path(out) / "reward.json"
        if run.returncode or not reward_file.is_file():
            return False, (f"verifier exit {run.returncode}, reward.json written: {reward_file.is_file()}, "
                           f"stderr: {run.stderr.strip()[-300:]}")
        reward = json.loads(reward_file.read_text())
    numeric = isinstance(reward, dict) and "reward" in reward and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in reward.values())
    return numeric, f"reward.json = {json.dumps(reward)}"


CHECKS = [check_schema_version, check_verifier_separate, check_verifier_dockerfile, check_verifier_not_solver,
          check_agent_user, check_dockerfile_user, check_image_has_no_task_files, check_solver_network_narrow,
          check_artifacts_declared, check_test_sh_wipes_reward, check_reference_reward_numeric]


def lint(task):
    task = Path(task).resolve()
    try:
        toml = tomllib.loads((task / "task.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        return [{"check": "task_toml", "status": "fail", "detail": str(error)}]
    rows = []
    for check in CHECKS:
        try:
            ok, detail = check(task, toml)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            ok, detail = False, f"{type(error).__name__}: {error}"
        rows.append({"check": check.__name__.removeprefix("check_"), "status": "ok" if ok else "fail",
                     "detail": detail})
    return rows


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.exit(__doc__)
    rows = lint(args[0])
    for row in rows:
        print(json.dumps(row))
    return 1 if any(row["status"] == "fail" for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
