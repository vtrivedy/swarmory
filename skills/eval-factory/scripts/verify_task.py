#!/usr/bin/env python3
"""Run a Harbor task's free checks on Docker or Modal and keep the proof in one evidence folder.

No check calls a model. Run this script with the Python environment that has Harbor installed:

    python verify_task.py doctor tasks/<task-name> [--env docker|modal]
    python verify_task.py free tasks/<task-name> [--env docker|modal] \\
        --endpoint https://<endpoint host>/anthropic \\
        --agent claude-code=anthropic/<model> --agent codex=openai/<model> \\
        [--smoke-model <model>] [--container-verifier] [--out <evidence folder>]
    python verify_task.py cleanup <evidence folder> [--env docker|modal] [--dry-run]

`doctor` changes nothing. It checks the Harbor version, task.toml, helper files, runtime auth, and git cleanliness
strictly inside the task folder. `free` writes summary.json, summary.md, one log per check, and every Harbor job
under jobs/. It exits 1 if any check fails or is skipped. `cleanup` stops only containers or Modal sandboxes whose
trials belong to that evidence folder.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
MODEL_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "DECISION_API_KEY")
HARBOR_APP = "__harbor__"
CACHE_PARTS = {"__pycache__", ".pytest_cache"}
MIN_HARBOR_VERSION = (0, 18)

CONTAINER_CASE_RUNNER = r"""
import json, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, "/tests")
import verifier

key = json.loads(Path("/tests/key.json").read_text())
cases = sorted(p for p in Path("/cases").iterdir() if p.is_dir())
failures = []
for case in cases:
    expected = json.loads((case / "expected.json").read_text())
    result = verifier.grade(case / "output", key)
    if result["reward"] != expected["reward"]:
        failures.append(f"{case.name}: reward {result['reward']} != {expected['reward']}")
    if "failed_check" in expected and result["failed_check"] != expected["failed_check"]:
        failures.append(f"{case.name}: failed_check {result['failed_check']!r} != {expected['failed_check']!r}")
    if "partial_score" in expected and result["partial_score"] != expected["partial_score"]:
        failures.append(f"{case.name}: partial_score {result['partial_score']} != {expected['partial_score']}")
    if "reward_without_hardening" in expected:
        unhardened = verifier.grade(case / "output", key, hardening=False)["reward"]
        if unhardened != expected["reward_without_hardening"]:
            failures.append(f"{case.name}: unhardened {unhardened} != {expected['reward_without_hardening']}")

ref_out = Path("/cases/reference/output")
with tempfile.TemporaryDirectory() as tmp:
    outputs = []
    for idx in range(2):
        folder = Path(tmp) / str(idx)
        folder.mkdir()
        subprocess.run([sys.executable, "-I", "/tests/verifier.py", str(ref_out), str(folder)], check=True)
        outputs.append((folder / "grade.json").read_bytes())
    if outputs[0] != outputs[1]:
        failures.append("reference: non-deterministic grade.json across fresh processes")

if failures:
    print("\n".join(failures), file=sys.stderr)
    sys.exit(1)
print(f"{len(cases)} container cases passed")
"""


def row(check, ok, detail, job=None, proof=None):
    status = ok if isinstance(ok, str) else "ok" if ok else "fail"
    return {
        "check": check,
        "status": status,
        "detail": detail,
        "job": job and str(job),
        "proof": proof and str(proof),
    }


def harbor_binary(override):
    if override:
        return override
    beside = Path(sys.executable).parent / "harbor"
    return str(beside) if beside.exists() else shutil.which("harbor") or "harbor"


def parse_version(text):
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def no_model_env(extra=None):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in MODEL_KEYS and not k.upper().endswith("_API_KEY")
    }
    env.update(extra or {})
    return env


def trial_results(job):
    return [(path.parent, json.loads(path.read_text())) for path in sorted(job.glob("*/result.json"))]


def one_trial(job):
    trials = trial_results(job)
    if len(trials) != 1:
        return None, None, f"expected 1 trial in {job}, found {len(trials)}"
    trial, result = trials[0]
    return trial, result, None


def exception_of(result):
    info = result.get("exception_info") or {}
    return info.get("exception_type") and f"{info['exception_type']}: {str(info.get('exception_message', ''))[:300]}"


def reward_of(result):
    return ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")


def judge_preflight(check, job, threshold, task_toml):
    trial, result, problem = one_trial(job)
    if problem:
        return row(check, False, problem, job)
    proof = trial / "agent/preflight.txt"
    if not proof.is_file():
        return row(check, False, f"no agent/preflight.txt, exception {exception_of(result)}", job)
    text = proof.read_text()
    checks = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    blocked = [c["check"] for c in checks if c.get("status") != "ok"]
    ok = bool(checks) and not blocked and "exit=0" in text
    detail = f"{len(checks)} checks, blocked: {blocked}, {text.splitlines()[0]}" if checks else "no check lines"
    return row(check, ok, detail, job, proof)


def judge_reward(expected):
    def judge(check, job, threshold, task_toml):
        trial, result, problem = one_trial(job)
        if problem:
            return row(check, False, problem, job)
        reward, error = reward_of(result), exception_of(result)
        grade = trial / "verifier/grade.json"
        proof = grade if grade.is_file() else trial / "result.json"
        failed = json.loads(grade.read_text()).get("failed_check") if grade.is_file() else "no grade.json"
        mode = result.get("verifier_environment_mode")
        if mode is None:
            mode = task_toml.get("verifier", {}).get("environment_mode")
            mode_label = f"{mode} (from task.toml)"
        else:
            mode_label = mode
        passed = reward is not None and (reward >= threshold if expected == "pass" else reward == 0)
        detail = f"reward {reward}, failed_check {failed!r}, verifier {mode_label}, exception {error}"
        return row(check, passed and mode == "separate" and not error, detail, job, proof)

    return judge


def judge_install(check, job, threshold, task_toml):
    trial, result, problem = one_trial(job)
    if problem:
        return row(check, False, problem, job)
    version, error = (result.get("agent_info") or {}).get("version"), exception_of(result)
    return row(check, bool(version) and not error, f"version {version}, exception {error}", job, trial / "result.json")


ENDPOINT_CHECK = """
python3 - "$ENDPOINT" <<'PY'
import json, sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=10)
    status, detail = "ok", "HTTP 200"
except urllib.error.HTTPError as error:
    status, detail = "ok", f"HTTP {error.code}"
except Exception as error:
    status, detail = "blocked", type(error).__name__
print(json.dumps({"check": "endpoint_reachable", "status": status, "detail": detail}))
sys.exit(status != "ok")
PY
"""


def endpoint_preflight(task, evidence, endpoint):
    """Run the task preflight, then an unauthenticated GET of the model endpoint."""
    script = evidence / "preflight-endpoint.sh"
    script.write_text(
        "#!/bin/bash\ncat > /tmp/task_preflight.sh <<'TASK_PREFLIGHT'\n"
        + (task / "preflight/preflight_checks.sh").read_text()
        + f"\nTASK_PREFLIGHT\nbash /tmp/task_preflight.sh\nstatus=$?\nENDPOINT={shlex.quote(endpoint)}\n"
        + ENDPOINT_CHECK
        + "[ $? = 0 ] || status=1\nexit $status\n"
    )
    return script


def harbor_checks(task, evidence, args):
    """Return (check name, extra harbor args, extra env, judge) for each Harbor job that free runs."""
    def preflight(script):
        return ["-a", "preflight_agent:PreflightAgent", "--ak", f"script={script}"]

    path_env = {"PYTHONPATH": str(HERE)}
    checks = [("preflight-no-network", preflight(task / "preflight/preflight_checks.sh"), path_env, judge_preflight)]
    if args.endpoint:
        allow = ["--allow-environment-host", urlparse(args.endpoint).hostname]
        checks.append(
            ("preflight-endpoint", preflight(endpoint_preflight(task, evidence, args.endpoint)) + allow, path_env, judge_preflight)
        )
    checks += [("oracle", ["-a", "oracle"], {}, judge_reward("pass")), ("nop", ["-a", "nop"], {}, judge_reward("zero"))]
    for spec in args.agent:
        name, model = spec.split("=", 1)
        checks.append((f"install-{name}", ["-a", name, "-m", model, "--install-only"], {}, judge_install))
    return checks


def run_harbor_check(task, evidence, args, task_toml, check, extra, env, judge):
    jobs = evidence / "jobs"
    command = [
        harbor_binary(args.harbor),
        "run",
        "-p",
        str(task),
        "--env",
        args.env,
        "-k",
        "1",
        "-n",
        "1",
        "-y",
        "-q",
        "--job-name",
        check,
        "-o",
        str(jobs),
        *[a for ek in args.ek for a in ("--ek", ek)],
        *extra,
    ]
    with open(evidence / f"{check}.log", "w") as log:
        log.write(" ".join(command) + "\n\n")
        log.flush()
        code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=no_model_env(env), check=False).returncode
    result = judge(check, jobs / check, args.oracle_threshold, task_toml)
    result["detail"] += f", harbor exit {code}"
    if not trial_results(jobs / check):
        tail = (evidence / f"{check}.log").read_text().strip().splitlines()
        if tail:
            result["detail"] += f", {tail[-1]}"
    return result


def run_local(evidence, check, command, cwd=None):
    proof = evidence / f"{check}.log"
    run = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    proof.write_text(" ".join(command) + "\n\n" + run.stdout + run.stderr)
    tail = (run.stdout + run.stderr).strip().splitlines()
    return run.returncode, proof, tail[-1] if tail else ""


def run_container_cases(task, evidence, pytest_cmd):
    """Verify stored cases inside the built tests/Dockerfile image plus static host pytest checks."""
    static_code, static_proof, static_tail = run_local(
        evidence,
        "task-tests-static",
        [
            *pytest_cmd.split(),
            "tests/",
            "-k",
            "test_required_cases_exist or test_instruction_states_every_graded_rule",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=task,
    )
    tag = f"ef-verify-{re.sub(r'[^a-z0-9_.-]', '-', task.name.lower())}-{os.getpid()}"
    proof = evidence / "task-tests.log"
    build = subprocess.run(["docker", "build", "-t", tag, str(task / "tests")], capture_output=True, text=True, check=False)
    if build.returncode != 0:
        proof.write_text(build.stdout + build.stderr)
        return 1, proof, f"verifier docker build failed (exit {build.returncode})"
    try:
        run = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{task / 'tests/cases'}:/cases:ro",
                tag,
                "python3",
                "-I",
                "-c",
                CONTAINER_CASE_RUNNER,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, check=False)
    proof.write_text(
        f"static pytest ({static_proof.name}): exit {static_code}, {static_tail}\n\n"
        + run.stdout
        + run.stderr
    )
    tail = (run.stdout + run.stderr).strip().splitlines()
    detail = tail[-1] if tail else f"container cases exit {run.returncode}"
    if static_code != 0:
        detail = f"static pytest failed ({static_tail}); {detail}"
    return (0 if static_code == 0 and run.returncode == 0 else 1), proof, detail


def git_state(task):
    """Check git cleanliness strictly inside the task folder (outside work/ records)."""
    def git(*argv):
        run = subprocess.run(["git", "-C", str(task), *argv], capture_output=True, text=True, check=False)
        return run.stdout.strip() if run.returncode == 0 else None

    commit = git("rev-parse", "--short", "HEAD")
    if not commit:
        return {"commit": None, "dirty": None, "uncommitted": []}
    status = git("status", "--porcelain", "--ignored", "--untracked-files=all", "--", ".").splitlines()
    uncommitted = [line[3:] for line in status if line[:2] in ("??", "!!") and not CACHE_PARTS & set(line.split("/"))]
    return {"commit": commit, "dirty": any(line[:2] not in ("??", "!!") for line in status), "uncommitted": uncommitted}


def harbor_accepts_row(task):
    """Ask Harbor's own Task loader whether the task directory is valid."""
    try:
        from harbor.models.task.config import TaskConfig
        from harbor.models.task.task import Task
    except ImportError:
        return row(
            "harbor_accepts",
            False,
            "harbor Python package not importable from this Python. Run verify_task.py with Harbor's Python.",
        )
    if Task.is_valid_dir(task):
        return row("harbor_accepts", True, "Harbor loads the task")
    try:
        TaskConfig.model_validate_toml((task / "task.toml").read_text())
        reason = "the config parses, but an input path or the tests folder is invalid"
    except (OSError, ValueError) as error:
        reason = " ".join(str(error).split())[:400]
    return row("harbor_accepts", False, reason)


def committed_row(state):
    """Harbor uploads the task folder on disk, so untracked or ignored files hide checkout failures."""
    if not state["commit"]:
        return row("committed", False, "the task folder is not in a git repo")
    stray = state["uncommitted"]
    return row(
        "committed",
        not stray and not state["dirty"],
        f"commit {state['commit']}, tracked edits: {state['dirty']}, untracked or ignored: {len(stray)} {stray[:5]}",
    )


def smoke_command(task, evidence, args):
    host = urlparse(args.endpoint).hostname if args.endpoint else "<model endpoint host>"
    agent_name = args.agent[0].split("=", 1)[0] if args.agent else "claude-code"
    if agent_name == "codex":
        env_flags = (
            f"--ae 'OPENAI_BASE_URL={args.endpoint or '<openai endpoint>'}' "
            f"--ae 'OPENAI_API_KEY=${{ENDPOINT_API_KEY}}'"
        )
    else:
        env_flags = (
            f"--ae 'ANTHROPIC_BASE_URL={args.endpoint or '<endpoint>'}' "
            f"--ae 'ANTHROPIC_API_KEY=${{ENDPOINT_API_KEY}}'"
        )
    return (
        f"harbor run -p {task} -a {agent_name} -m {args.smoke_model} -k 1 -n 1 --env {args.env} "
        f"--allow-environment-host {host} {env_flags} --job-name smoke -o {evidence / 'jobs'}"
        + "".join(f" --ek {shlex.quote(ek)}" for ek in args.ek)
    )


def write_summary(evidence, meta, rows):
    (evidence / "summary.json").write_text(json.dumps({**meta, "checks": rows}, indent=1))
    lines = [
        f"# Free checks: {meta['task_name']}",
        "",
        (
            f"Task `{meta['task']}` at commit {meta['git']['commit']} (dirty: {meta['git']['dirty']}), "
            f"Harbor {meta['harbor_version']}, env {meta['env']}, {meta['started']} to {meta['finished']}."
        ),
        "",
        "| Check | Status | Detail | Job | Proof |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['check']} | {r['status']} | {r['detail'].replace('|', '/')} | "
        f"{Path(r['job']).relative_to(evidence) if r['job'] else ''} | {r.get('proof') or ''} |"
        for r in rows
    ]
    lines += ["", "summarize_runs.py, one job at a time:", "", "```", *meta["summarize_runs"], "```"]
    lines += ["", "Not run. The smoke run calls a model. Run it next:", "", "```bash", meta["smoke_command"], "```", ""]
    (evidence / "summary.md").write_text("\n".join(lines))


def free(args):
    task = Path(args.task).resolve()
    toml = tomllib.loads((task / "task.toml").read_text())
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence = Path(args.out or f"verify-runs/{task.name}-{stamp}").resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    print(f"evidence: {evidence}", flush=True)
    version = subprocess.run(
        [harbor_binary(args.harbor), "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    state = git_state(task)
    meta = {
        "task": str(task),
        "task_name": toml.get("task", {}).get("name", task.name),
        "git": state,
        "harbor_version": version,
        "env": args.env,
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
        "smoke_command": smoke_command(task, evidence, args),
        "finished": None,
    }
    (evidence / "summary.json").write_text(json.dumps({**meta, "checks": []}, indent=1))

    if args.container_verifier:
        code, proof, tail = run_container_cases(task, evidence, args.pytest)
    else:
        code, proof, tail = run_local(
            evidence, "task-tests", [*args.pytest.split(), "tests/", "-q", "-p", "no:cacheprovider"], cwd=task
        )
    rows = [committed_row(state), harbor_accepts_row(task), row("task-tests", code == 0, tail, proof=proof)]
    lint_cmd = [sys.executable, str(HERE / "lint_task.py"), str(task)]
    if args.container_verifier:
        lint_cmd.append("--container-verifier")
    code, proof, _ = run_local(evidence, "lint", lint_cmd)
    failed = [
        json.loads(line)["check"]
        for line in proof.read_text().splitlines()
        if line.startswith("{") and json.loads(line)["status"] != "ok"
    ]
    rows.append(row("lint", code == 0, f"failed: {failed}", proof=proof))
    if not args.endpoint:
        rows.append(row("preflight-endpoint", "skip", "no --endpoint given, so the solver network is unchecked"))
    if not args.agent:
        rows.append(row("install", "skip", "no --agent given"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(run_harbor_check, task, evidence, args, toml, *spec)
            for spec in harbor_checks(task, evidence, args)
        ]
        rows += [future.result() for future in futures]

    summarize = [sys.executable, str(HERE / "summarize_runs.py")]
    meta["summarize_runs"] = [
        run_local(evidence, f"summarize-{check}", [*summarize, str(evidence / "jobs" / check)])[2]
        for check in ("oracle", "nop")
    ]
    meta["finished"] = datetime.now(UTC).isoformat(timespec="seconds")
    write_summary(evidence, meta, rows)
    for r in rows:
        print(f"{r['status']:5} {r['check']:22} {r['detail']}")
    print(f"summary: {evidence / 'summary.md'}")
    return 1 if any(r["status"] in ("fail", "skip") for r in rows) else 0


def doctor(args):
    task = Path(args.task).resolve()
    rows = []
    version = subprocess.run(
        [harbor_binary(args.harbor), "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    parsed = parse_version(version)
    version_ok = parsed is not None and parsed[:2] >= MIN_HARBOR_VERSION
    rows.append(row("harbor", version_ok, f"harbor {version or 'not found'}, requires >= 0.18"))
    try:
        toml = tomllib.loads((task / "task.toml").read_text())
        name = toml.get("task", {}).get("name")
        placeholder = isinstance(name, str) and ("<" in name or ">" in name)
        rows.append(
            row(
                "task_toml",
                not placeholder,
                f"name {name!r} still has placeholders" if placeholder else f"name {name!r}",
            )
        )
    except (OSError, tomllib.TOMLDecodeError) as error:
        rows.append(row("task_toml", False, str(error)))
    for path in (task / "preflight/preflight_checks.sh", HERE / "preflight_agent.py", HERE / "summarize_runs.py"):
        rows.append(row(f"file:{path.name}", path.is_file(), str(path)))
    if args.env == "modal":
        try:
            running = harbor_sandboxes()
            rows.append(row("modal", True, f"authenticated, {len(running)} Harbor sandboxes running in {HARBOR_APP}"))
        except Exception as error:  # noqa: BLE001
            rows.append(row("modal", False, f"{type(error).__name__}: {error}"))
    else:
        probe = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
        rows.append(row("docker", probe.returncode == 0, "docker daemon reachable" if probe.returncode == 0 else probe.stderr.strip()[:200]))
    rows += [harbor_accepts_row(task), committed_row(git_state(task))]
    for r in rows:
        print(json.dumps({k: r[k] for k in ("check", "status", "detail")}))
    return 1 if any(r["status"] == "fail" for r in rows) else 0


def harbor_sandboxes():
    import modal

    try:
        app = modal.App.lookup(HARBOR_APP)
    except modal.exception.NotFoundError:
        return []
    return list(modal.Sandbox.list(app_id=app.app_id))


def cleanup(args):
    evidence = Path(args.evidence).resolve()
    trials = {path.name for path in evidence.glob("jobs/*/*") if path.is_dir()}
    stopped = 0
    if args.env == "modal":
        for sandbox in harbor_sandboxes():
            session = sandbox.get_tags().get("harbor.session_id", "")
            if any(session.startswith(f"{trial}__") for trial in trials):
                if not args.dry_run:
                    sandbox.terminate()
                stopped += 1
                print(json.dumps({"sandbox": session, "action": "would stop" if args.dry_run else "stopped"}))
    else:
        ps = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.ID}} {{.Names}}"], capture_output=True, text=True, check=False
        )
        for line in ps.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            cid, cname = parts
            if any(trial.lower() in cname.lower() for trial in trials):
                if not args.dry_run:
                    subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)
                stopped += 1
                print(json.dumps({"container": cname, "action": "would remove" if args.dry_run else "removed"}))
    summary = evidence / "summary.json"
    finished = summary.is_file() and json.loads(summary.read_text()).get("finished") is not None
    result = {
        "env": args.env,
        "trials": len(trials),
        "stopped": stopped,
        "dry_run": args.dry_run,
        "evidence_kept": (evidence / "jobs").is_dir(),
        "run_finished": finished,
        "evidence": str(evidence),
    }
    with open(evidence / "cleanup.log", "a") as log:
        log.write(json.dumps(result) + "\n")
    print(json.dumps(result))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--harbor", help="Harbor command, defaulting to the harbor binary beside this Python")
    sub = parser.add_subparsers(dest="command", required=True)

    doc = sub.add_parser("doctor")
    doc.add_argument("task")
    doc.add_argument("--env", choices=("docker", "modal"), default="docker")

    run = sub.add_parser("free")
    run.add_argument("task")
    run.add_argument("--env", choices=("docker", "modal"), default="docker")
    run.add_argument("--out", help="evidence folder, defaulting to verify-runs/<task>-<UTC time>")
    run.add_argument(
        "--endpoint",
        help="model endpoint base URL the solver will use. Its host is allowed in the second preflight.",
    )
    run.add_argument(
        "--agent",
        action="append",
        default=[],
        metavar="AGENT=MODEL",
        help="agent CLI to install-check, such as claude-code=anthropic/<model>. Repeatable.",
    )
    run.add_argument("--pytest", default="uvx pytest", help="command that runs the task tests")
    run.add_argument(
        "--ek",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="environment kwarg for every Harbor job, such as a Modal keepalive launcher",
    )
    run.add_argument(
        "--smoke-model",
        default="<a small model from the endpoint model list>",
        help="model for the smoke-run command written to summary.md, exactly as the endpoint lists it",
    )
    run.add_argument("--oracle-threshold", type=float, default=1.0, help="reward the oracle must reach")
    run.add_argument(
        "--container-verifier",
        action="store_true",
        help="run stored cases inside the built verifier image and skip host verifier execution in lint_task.py",
    )

    clean = sub.add_parser("cleanup")
    clean.add_argument("evidence")
    clean.add_argument("--env", choices=("docker", "modal"), default="docker")
    clean.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    return {"doctor": doctor, "free": free, "cleanup": cleanup}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
