#!/usr/bin/env python3
"""Run a Harbor task's free checks on Modal and keep the proof in one evidence folder. No check calls a model.

Run it with the Python that has Harbor installed, so `harbor` and `modal` resolve from the same environment:

    python verify_task.py doctor tasks/<task-name>
    python verify_task.py free tasks/<task-name> --endpoint https://<endpoint host>/anthropic \\
        --harness claude-code=anthropic/<model> --harness codex=openai/<model> [--out <evidence folder>]
    python verify_task.py cleanup <evidence folder> [--dry-run]

`free` writes <evidence folder>/summary.json, summary.md, one log per check and every Harbor job under jobs/. It
exits 1 if any check fails. `cleanup` stops only the Modal sandboxes whose trials are in that evidence folder.
"""
import argparse
import json
import os
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
MODEL_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
HARBOR_APP = "__harbor__"
SMOKE_MODEL = "claude-haiku-5-5"  # bare: with ANTHROPIC_BASE_URL set, Harbor sends it as written
CACHE_PARTS = {"__pycache__", ".pytest_cache"}


def row(check, ok, detail, job=None, proof=None):
    status = ok if isinstance(ok, str) else "ok" if ok else "fail"
    return {"check": check, "status": status, "detail": detail, "job": job and str(job),
            "proof": proof and str(proof)}


def harbor_binary(override):
    if override:
        return override
    beside = Path(sys.executable).parent / "harbor"
    return str(beside) if beside.exists() else shutil.which("harbor") or "harbor"


def no_model_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in MODEL_KEYS}
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


def judge_preflight(check, job, threshold):
    trial, result, problem = one_trial(job)
    if problem:
        return row(check, False, problem, job)
    proof = trial / "agent/preflight.txt"
    if not proof.is_file():
        return row(check, False, f"no agent/preflight.txt; {exception_of(result)}", job)
    text = proof.read_text()
    checks = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    blocked = [c["check"] for c in checks if c.get("status") != "ok"]
    ok = bool(checks) and not blocked and "exit=0" in text
    detail = f"{len(checks)} checks, blocked: {blocked}; {text.splitlines()[0]}" if checks else "no check lines"
    return row(check, ok, detail, job, proof)


def judge_reward(expected):
    def judge(check, job, threshold):
        trial, result, problem = one_trial(job)
        if problem:
            return row(check, False, problem, job)
        reward, error = reward_of(result), exception_of(result)
        grade = trial / "verifier/grade.json"
        proof = grade if grade.is_file() else trial / "result.json"
        failed = json.loads(grade.read_text()).get("failed_check") if grade.is_file() else "no grade.json"
        mode = result.get("verifier_environment_mode")
        passed = reward is not None and (reward >= threshold if expected == "pass" else reward == 0)
        detail = f"reward {reward}, failed_check {failed!r}, verifier {mode}, exception {error}"
        return row(check, passed and mode == "separate" and not error, detail, job, proof)
    return judge


def judge_install(check, job, threshold):
    trial, result, problem = one_trial(job)
    if problem:
        return row(check, False, problem, job)
    version, error = (result.get("agent_info") or {}).get("version"), exception_of(result)
    return row(check, bool(version) and not error, f"version {version}, exception {error}", job,
               trial / "result.json")


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
    """The task's preflight, then an unauthenticated GET of the model endpoint. Any HTTP status proves the solver
    can reach it, and no key means no model call."""
    script = evidence / "preflight-endpoint.sh"
    script.write_text("#!/bin/bash\ncat > /tmp/task_preflight.sh <<'TASK_PREFLIGHT'\n"
                      + (task / "preflight/preflight_checks.sh").read_text()
                      + f"\nTASK_PREFLIGHT\nbash /tmp/task_preflight.sh; status=$?\nENDPOINT={shlex.quote(endpoint)}\n"
                      + ENDPOINT_CHECK + "[ $? = 0 ] || status=1\nexit $status\n")
    return script


def harbor_checks(task, evidence, args):
    """(check name, extra harbor args, extra env, judge) for each Harbor job `free` runs."""
    def preflight(script):
        return ["-a", "preflight_agent:PreflightAgent", "--ak", f"script={script}"]
    path_env = {"PYTHONPATH": str(HERE)}
    checks = [("preflight-no-network", preflight(task / "preflight/preflight_checks.sh"), path_env, judge_preflight)]
    if args.endpoint:
        allow = ["--allow-environment-host", urlparse(args.endpoint).hostname]
        checks.append(("preflight-endpoint", preflight(endpoint_preflight(task, evidence, args.endpoint)) + allow,
                       path_env, judge_preflight))
    checks += [("oracle", ["-a", "oracle"], {}, judge_reward("pass")), ("nop", ["-a", "nop"], {}, judge_reward("zero"))]
    for spec in args.harness:
        name, model = spec.split("=", 1)
        checks.append((f"install-{name}", ["-a", name, "-m", model, "--install-only"], {}, judge_install))
    return checks


def run_harbor_check(task, evidence, args, check, extra, env, judge):
    jobs = evidence / "jobs"
    command = [harbor_binary(args.harbor), "run", "-p", str(task), "--env", "modal", "-k", "1", "-n", "1", "-y", "-q",
               "--job-name", check, "-o", str(jobs), *extra]
    with open(evidence / f"{check}.log", "w") as log:
        log.write(" ".join(command) + "\n\n")
        log.flush()
        code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=no_model_env(env),
                              check=False).returncode
    result = judge(check, jobs / check, args.oracle_threshold)
    result["detail"] += f"; harbor exit {code}"
    if not trial_results(jobs / check):
        result["detail"] += f"; {(evidence / f'{check}.log').read_text().strip().splitlines()[-1]}"
    return result


def run_local(evidence, check, command, cwd=None):
    proof = evidence / f"{check}.log"
    run = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    proof.write_text(" ".join(command) + "\n\n" + run.stdout + run.stderr)
    tail = (run.stdout + run.stderr).strip().splitlines()
    return run.returncode, proof, tail[-1] if tail else ""


def git_state(task):
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
    """Harbor treats a folder it can't load as an empty dataset and fails with "Either datasets or tasks must be
    provided", so ask its own loader why."""
    from harbor.models.task.config import TaskConfig
    from harbor.models.task.task import Task
    if Task.is_valid_dir(task):
        return row("harbor_accepts", True, "Harbor loads the task")
    try:
        TaskConfig.model_validate_toml((task / "task.toml").read_text())
        reason = "the config parses, but an input path or the tests folder is invalid"
    except (OSError, ValueError) as error:
        reason = " ".join(str(error).split())[:400]
    return row("harbor_accepts", False, reason)


def committed_row(state):
    """Harbor uploads the folder on disk. A file that is untracked or ignored there is missing from a clean checkout,
    so a check that passes only because of it proves nothing about the committed task."""
    if not state["commit"]:
        return row("committed", False, "the task folder is not in a git repo")
    stray = state["uncommitted"]
    return row("committed", not stray, f"commit {state['commit']}, tracked edits: {state['dirty']}, "
                                       f"untracked or ignored: {len(stray)} {stray[:5]}")


def smoke_command(task, evidence, args):
    host = urlparse(args.endpoint).hostname if args.endpoint else "<model endpoint host>"
    return (f"harbor run -p {task} -a claude-code -m {SMOKE_MODEL} -k 1 -n 1 --env modal "
            f"--allow-environment-host {host} --ae 'ANTHROPIC_BASE_URL={args.endpoint or '<endpoint>'}' "
            f"--ae 'ANTHROPIC_API_KEY=${{ROUTE_KEY}}' --job-name smoke -o {evidence / 'jobs'}")


def write_summary(evidence, meta, rows):
    (evidence / "summary.json").write_text(json.dumps({**meta, "checks": rows}, indent=1))
    lines = [f"# Free checks: {meta['task_name']}", "",
             (f"Task `{meta['task']}` at commit {meta['git']['commit']} (dirty: {meta['git']['dirty']}), "
              f"Harbor {meta['harbor_version']}, {meta['started']} to {meta['finished']}."), "",
             "| Check | Status | Detail | Job |", "|---|---|---|---|"]
    lines += [f"| {r['check']} | {r['status']} | {r['detail'].replace('|', '/')} | "
              f"{Path(r['job']).relative_to(evidence) if r['job'] else ''} |" for r in rows]
    lines += ["", "summarize_runs.py, one job at a time:", "", "```", *meta["summarize_runs"], "```"]
    lines += ["", "Not run: the smoke run calls a model. Ask the run owner before running:", "", "```bash",
              meta["smoke_command"], "```", ""]
    (evidence / "summary.md").write_text("\n".join(lines))


def free(args):
    task = Path(args.task).resolve()
    toml = tomllib.loads((task / "task.toml").read_text())
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence = Path(args.out or f"verify-runs/{task.name}-{stamp}").resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    print(f"evidence: {evidence}", flush=True)
    version = subprocess.run([harbor_binary(args.harbor), "--version"], capture_output=True, text=True,
                             check=False).stdout.strip()
    state = git_state(task)
    meta = {"task": str(task), "task_name": toml.get("task", {}).get("name", task.name), "git": state,
            "harbor_version": version, "started": datetime.now(UTC).isoformat(timespec="seconds"),
            "smoke_command": smoke_command(task, evidence, args), "finished": None}
    (evidence / "summary.json").write_text(json.dumps({**meta, "checks": []}, indent=1))

    code, proof, tail = run_local(evidence, "task-tests", [*args.pytest.split(), "tests/", "-q", "-p",
                                                           "no:cacheprovider"], cwd=task)
    rows = [committed_row(state), harbor_accepts_row(task), row("task-tests", code == 0, tail, proof=proof)]
    code, proof, _ = run_local(evidence, "lint", [sys.executable, str(HERE / "lint_task.py"), str(task)])
    failed = [json.loads(line)["check"] for line in proof.read_text().splitlines() if line.startswith("{")
              and json.loads(line)["status"] != "ok"]
    rows.append(row("lint", code == 0, f"failed: {failed}", proof=proof))
    if not args.endpoint:
        rows.append(row("preflight-endpoint", "skip", "no --endpoint given, so the solver's run network is unchecked"))
    if not args.harness:
        rows.append(row("install", "skip", "no --harness given"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run_harbor_check, task, evidence, args, *spec)
                   for spec in harbor_checks(task, evidence, args)]
        rows += [future.result() for future in futures]

    summarize = [sys.executable, str(HERE / "summarize_runs.py")]
    meta["summarize_runs"] = [run_local(evidence, f"summarize-{check}", [*summarize, str(evidence / "jobs" / check)])[2]
                              for check in ("oracle", "nop")]
    meta["finished"] = datetime.now(UTC).isoformat(timespec="seconds")
    write_summary(evidence, meta, rows)
    for r in rows:
        print(f"{r['status']:5} {r['check']:22} {r['detail']}")
    print(f"summary: {evidence / 'summary.md'}")
    return 1 if any(r["status"] == "fail" for r in rows) else 0


def doctor(args):
    task = Path(args.task).resolve()
    rows = []
    version = subprocess.run([harbor_binary(args.harbor), "--version"], capture_output=True, text=True,
                             check=False).stdout.strip()
    rows.append(row("harbor", version.startswith("0.23."), f"harbor {version or 'not found'}; this skill targets 0.23"))
    try:
        toml = tomllib.loads((task / "task.toml").read_text())
        rows.append(row("task_toml", True, f"name {toml.get('task', {}).get('name')!r}"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        rows.append(row("task_toml", False, str(error)))
    for path in (task / "preflight/preflight_checks.sh", HERE / "preflight_agent.py",
                 HERE / "summarize_runs.py"):
        rows.append(row(f"file:{path.name}", path.is_file(), str(path)))
    try:
        running = harbor_sandboxes()
        rows.append(row("modal", True, f"authenticated; {len(running)} Harbor sandboxes running in app {HARBOR_APP}"))
    except Exception as error:  # noqa: BLE001
        rows.append(row("modal", False, f"{type(error).__name__}: {error}"))
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
    for sandbox in harbor_sandboxes():
        session = sandbox.get_tags().get("harbor.session_id", "")
        if any(session.startswith(f"{trial}__") for trial in trials):
            if not args.dry_run:
                sandbox.terminate()
            stopped += 1
            print(json.dumps({"sandbox": session, "action": "would stop" if args.dry_run else "stopped"}))
    summary = evidence / "summary.json"
    finished = summary.is_file() and json.loads(summary.read_text()).get("finished") is not None
    result = {"trials": len(trials), "sandboxes": stopped, "dry_run": args.dry_run,
              "evidence_kept": (evidence / "jobs").is_dir(), "run_finished": finished, "evidence": str(evidence)}
    with open(evidence / "cleanup.log", "a") as log:
        log.write(json.dumps(result) + "\n")
    print(json.dumps(result))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--harbor", help="Harbor command; default is the harbor beside this Python")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor").add_argument("task")
    run = sub.add_parser("free")
    run.add_argument("task")
    run.add_argument("--out", help="evidence folder; default verify-runs/<task>-<UTC time>")
    run.add_argument("--endpoint", help="the model endpoint base URL the solver will use; its host is allowed in the "
                                        "second preflight")
    run.add_argument("--harness", action="append", default=[], metavar="AGENT=MODEL",
                     help="a harness to install-check, such as claude-code=anthropic/<model>; repeatable")
    run.add_argument("--pytest", default="uvx pytest", help="command that runs the task's own tests")
    run.add_argument("--oracle-threshold", type=float, default=1.0, help="reward the oracle must reach")
    clean = sub.add_parser("cleanup")
    clean.add_argument("evidence")
    clean.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return {"doctor": doctor, "free": free, "cleanup": cleanup}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
