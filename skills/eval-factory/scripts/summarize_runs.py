"""Summarize Harbor jobs as pass, fail, refusal, or infrastructure failure per task and model.

    python3 summarize_runs.py jobs/run-a jobs/run-b
    python3 summarize_runs.py jobs/run-a --first 3 --infrastructure-failure link-01__abc123

A run with no grade is an infrastructure failure. A run with any agent error other than hitting the time limit
or context limit is also an infrastructure failure. Infrastructure failures are excluded from the pass rate so
you can rerun them. A refusal (AgentSafetyRefusalError) keeps its own status, stays in the denominator, and
never counts as a pass.

Some failures appear only in the agent log, such as a stall in an agent that exits 0. Name those trials with
--infrastructure-failure. A check in verifier/grade.json whose id starts with "decoy_" and failed means the
run took the decoy. Each row counts those runs, reports mean partial_score and mean cost conditioned on pass
versus fail when available, and with --json also reports pass_any and pass_all.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

HERE = str(Path(__file__).resolve().parent)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from probe_odds import wilson  # noqa: E402

CLOCK = "AgentTimeoutError"
SCORED_ERRORS = {
    CLOCK: "time_limit",
    "ContextWindowExceededError": "context_window",
    "ContextLengthExceededError": "context_window",
}
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


def cost_of(result):
    """Extract total USD cost from agent_result or step_results when recorded."""
    agent_result = result.get("agent_result") or {}
    cost = agent_result.get("cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        return float(cost)
    steps = result.get("step_results") or []
    step_costs = [
        float((s.get("agent_result") or {})["cost_usd"])
        for s in steps
        if isinstance((s.get("agent_result") or {}).get("cost_usd"), (int, float))
        and not isinstance((s.get("agent_result") or {}).get("cost_usd"), bool)
    ]
    return sum(step_costs) if step_costs else None


def grade_metadata(trial_dir, result):
    """Read decoy_taken and partial_score from verifier/grade.json or verifier_result."""
    grade = trial_dir / "verifier" / "grade.json"
    decoy = False
    partial = None
    if grade.is_file():
        data = json.loads(grade.read_text())
        checks = data.get("checks") or []
        decoy = any(c.get("id", "").startswith("decoy_") and not c.get("passed") for c in checks)
        if isinstance(data.get("partial_score"), (int, float)) and not isinstance(data.get("partial_score"), bool):
            partial = float(data["partial_score"])
    if partial is None:
        rewards = (result.get("verifier_result") or {}).get("rewards") or {}
        if isinstance(rewards.get("partial_score"), (int, float)) and not isinstance(rewards.get("partial_score"), bool):
            partial = float(rewards["partial_score"])
    return decoy, partial


def trials(job_dirs):
    for job in job_dirs:
        for path in sorted(Path(job).glob("*/result.json")):
            result = json.loads(path.read_text())
            decoy, partial = grade_metadata(path.parent, result)
            yield {**result, "decoy_taken": decoy, "partial_score": partial, "cost_usd": cost_of(result)}


def summarize(job_dirs, threshold=1.0, first=None, forced=()):
    cells = defaultdict(list)
    for result in sorted(trials(job_dirs), key=lambda r: r.get("started_at") or ""):
        agent = (result.get("config") or {}).get("agent") or {}
        runner = agent.get("name") or (agent.get("import_path") or "agent").rsplit(":", 1)[-1]
        model = f"{runner} {agent['model_name']}" if agent.get("model_name") else runner
        status, reason = classify(result, threshold, set(forced))
        cells[(result.get("task_name"), model)].append(
            {
                "trial": result.get("trial_name"),
                "status": status,
                "reason": reason,
                "decoy_taken": result["decoy_taken"],
                "partial_score": result["partial_score"],
                "cost_usd": result["cost_usd"],
            }
        )
    rows = []
    for (task, model), attempts in sorted(cells.items()):
        clean = [a for a in attempts if a["status"] != "infrastructure_failure"]
        scored = clean[:first]
        passes = sum(a["status"] == "pass" for a in scored)
        low, high = wilson(passes, len(scored))
        partials = [a["partial_score"] for a in scored if a["partial_score"] is not None]
        mean_partial = round(sum(partials) / len(partials), 3) if partials else None
        pass_costs = [a["cost_usd"] for a in scored if a["status"] == "pass" and a["cost_usd"] is not None]
        fail_costs = [a["cost_usd"] for a in scored if a["status"] != "pass" and a["cost_usd"] is not None]
        mean_cost_pass = round(sum(pass_costs) / len(pass_costs), 4) if pass_costs else None
        mean_cost_fail = round(sum(fail_costs) / len(fail_costs), 4) if fail_costs else None
        rows.append(
            {
                "task": task,
                "model": model,
                "attempts": len(attempts),
                "scored": len(scored),
                "passes": passes,
                "pass_any": passes > 0,
                "pass_all": bool(scored) and passes == len(scored),
                "mean_partial_score": mean_partial,
                "mean_cost_pass": mean_cost_pass,
                "mean_cost_fail": mean_cost_fail,
                "refusals": sum(a["status"] == "refusal" for a in scored),
                "decoys_taken": sum(a["decoy_taken"] for a in scored),
                "infrastructure_failures": len(attempts) - len(clean),
                "interval": [round(low, 3), round(high, 3)],
                "trials": attempts,
            }
        )
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jobs", nargs="+", help="Harbor job folders")
    parser.add_argument("--threshold", type=float, default=1.0, help="reward that counts as a pass")
    parser.add_argument("--first", type=int, help="keep only the first N scored runs per task and model, in start order")
    parser.add_argument(
        "--infrastructure-failure",
        nargs="*",
        default=[],
        metavar="TRIAL",
        help="trial names whose trace shows an infrastructure failure",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows = summarize(args.jobs, args.threshold, args.first, args.infrastructure_failure)
    if args.json:
        print(json.dumps(rows, indent=1))
        return
    for r in rows:
        low, high = r["interval"]
        partial_str = f"\tpartial {r['mean_partial_score']:.2f}" if r["mean_partial_score"] is not None else ""
        cost_parts = []
        if r["mean_cost_pass"] is not None:
            cost_parts.append(f"pass ${r['mean_cost_pass']:.2f}")
        if r["mean_cost_fail"] is not None:
            cost_parts.append(f"fail ${r['mean_cost_fail']:.2f}")
        cost_str = f"\tcost {', '.join(cost_parts)}" if cost_parts else ""
        print(
            f"{r['task']}\t{r['model']}\t{r['passes']} of {r['scored']}\t{low:.0%} to {high:.0%}"
            f"{partial_str}{cost_str}\trefusals {r['refusals']}\tdecoys taken {r['decoys_taken']}"
            f"\tinfrastructure failures {r['infrastructure_failures']}"
        )


if __name__ == "__main__":
    main()
