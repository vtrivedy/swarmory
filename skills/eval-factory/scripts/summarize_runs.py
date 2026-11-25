"""Summarize Harbor jobs as pass, fail, refusal, or infrastructure failure, per task and model.

    python3 summarize_runs.py jobs/run-a jobs/run-b
    python3 summarize_runs.py jobs/run-a --first 3 --infrastructure-failure link-01__abc123

A run with no grade is an infrastructure failure. So is a run with any harness error except exceeding the task's time
limit or the model's context window, which are scored fails. Infrastructure failures are left out of the pass rate, so you can rerun them.
A refusal (AgentSafetyRefusalError) has its own status. It stays in the denominator and never counts as a pass.

Some failures show only in the harness log, such as a stall in a harness that exits 0. Name those runs with
--infrastructure-failure. A check in verifier/grade.json whose id starts with "decoy_" and failed means the run took the
decoy. Each row counts those runs, and with --json also reports whether any run passed (pass_any) and whether all passed (pass_all).
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path

from probe_odds import wilson

CLOCK = "AgentTimeoutError"
SCORED_ERRORS = {CLOCK: "time_limit", "ContextWindowExceededError": "context_window"}
REFUSAL = "AgentSafetyRefusalError"


def classify(result, threshold, forced):
    """Return (status, reason) for one trial's result.json."""
    if result.get("trial_name") in forced:
        return "infrastructure_failure", "named on the command line"
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    exception = (result.get("exception_info") or {}).get("exception_type")
    if exception == REFUSAL:
        return "refusal", exception
    if "reward" not in rewards:
        return "infrastructure_failure", exception or "no grade"
    if exception and exception not in SCORED_ERRORS:
        return "infrastructure_failure", exception
    if rewards["reward"] >= threshold:
        return "pass", "solved"
    return "fail", SCORED_ERRORS.get(exception, "graded")


def took_decoy(trial_dir):
    grade = trial_dir / "verifier" / "grade.json"
    if not grade.is_file():
        return False
    checks = json.loads(grade.read_text()).get("checks") or []
    return any(c.get("id", "").startswith("decoy_") and not c.get("passed") for c in checks)


def trials(job_dirs):
    for job in job_dirs:
        for path in sorted(Path(job).glob("*/result.json")):
            yield {**json.loads(path.read_text()), "decoy_taken": took_decoy(path.parent)}


def summarize(job_dirs, threshold=1.0, first=None, forced=()):
    cells = defaultdict(list)
    for result in sorted(trials(job_dirs), key=lambda r: r.get("started_at") or ""):
        agent = (result.get("config") or {}).get("agent") or {}
        harness = agent.get("name") or (agent.get("import_path") or "agent").rsplit(":", 1)[-1]
        model = f"{harness} {agent['model_name']}" if agent.get("model_name") else harness
        status, reason = classify(result, threshold, set(forced))
        cells[(result.get("task_name"), model)].append({"trial": result.get("trial_name"), "status": status,
                                                         "reason": reason, "decoy_taken": result["decoy_taken"]})
    rows = []
    for (task, model), attempts in sorted(cells.items()):
        clean = [a for a in attempts if a["status"] != "infrastructure_failure"]
        scored = clean[:first]
        passes = sum(a["status"] == "pass" for a in scored)
        low, high = wilson(passes, len(scored))
        rows.append({"task": task, "model": model, "attempts": len(attempts), "scored": len(scored),
                     "passes": passes, "pass_any": passes > 0, "pass_all": bool(scored) and passes == len(scored), "refusals": sum(a["status"] == "refusal" for a in scored),
                     "decoys_taken": sum(a["decoy_taken"] for a in scored),
                     "infrastructure_failures": len(attempts) - len(clean),
                     "interval": [round(low, 3), round(high, 3)], "trials": attempts})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jobs", nargs="+", help="Harbor job folders")
    parser.add_argument("--threshold", type=float, default=1.0, help="reward that counts as a pass")
    parser.add_argument("--first", type=int, help="keep only the first N scored runs per task and model, in start order")
    parser.add_argument("--infrastructure-failure", nargs="*", default=[], metavar="TRIAL",
                        help="trial names whose failure the trace shows was the environment's")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows = summarize(args.jobs, args.threshold, args.first, args.infrastructure_failure)
    if args.json:
        print(json.dumps(rows, indent=1))
        return
    for r in rows:
        low, high = r["interval"]
        print(f"{r['task']}\t{r['model']}\t{r['passes']} of {r['scored']}\t{low:.0%} to {high:.0%}"
              f"\trefusals {r['refusals']}\tdecoys taken {r['decoys_taken']}"
              f"\tinfrastructure failures {r['infrastructure_failures']}")


if __name__ == "__main__":
    main()
