"""Intervals and decision odds for small numbers of runs.

    python3 probe_odds.py wilson 2 8
    python3 probe_odds.py rule --attempts 4 --kill-at 3
    python3 probe_odds.py rule --attempts 32 --keep 9 15
    python3 probe_odds.py set summary.json
    python3 probe_odds.py paired before.json after.json

`wilson` gives the 95% interval for one task's pass count. `rule` shows how often a keep or kill rule fires at each
true pass rate, so you can choose the rule before the runs. `set` reads one `summarize_runs.py --json` file made with
`--first N`. Per model, it gives the mean of per-task pass rates with a task-clustered standard error, a 95% interval
using the Student t distribution for small task counts, plus pass@k and pass^k. It needs at least two tasks with equal
runs each. `paired` reads two such files, one model or agent version each, and gives the 95% t-interval of the mean
per-task difference in pass rate (after minus before) over the tasks both scored.
"""
import argparse
import json
from math import comb, sqrt
from pathlib import Path
from statistics import mean, stdev

RATES = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)

# Two-sided 95% Student t critical values for degrees of freedom 1 through 29.
T_CRITICAL_95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045,
}


def t_critical_95(n):
    """Return the two-sided 95% t critical value for sample size n (normal 1.96 for df >= 30)."""
    df = max(1, n - 1)
    return T_CRITICAL_95.get(df, 1.96)


def wilson(passes, attempts, z=1.96):
    """95% Wilson interval for a pass rate."""
    if attempts == 0:
        return 0.0, 1.0
    p = passes / attempts
    centre = (p + z * z / (2 * attempts)) / (1 + z * z / attempts)
    half = z * sqrt(p * (1 - p) / attempts + z * z / (4 * attempts * attempts)) / (1 + z * z / attempts)
    return max(0.0, centre - half), min(1.0, centre + half)


def chance(rate, attempts, low, high):
    """Chance that the pass count lands in [low, high] when the true rate is `rate`."""
    return sum(comb(attempts, k) * rate ** k * (1 - rate) ** (attempts - k) for k in range(low, high + 1))


def pass_rates(path):
    """Return {task: pass rate} from one summarize_runs.py --json output."""
    rates = {}
    for row in json.loads(Path(path).read_text()):
        if row["task"] in rates:
            raise SystemExit(f"{path} has more than one row for {row['task']}. Give each file one model.")
        if row["scored"]:
            rates[row["task"]] = row["passes"] / row["scored"]
    return rates


def paired(before, after, z=None):
    """Return per-task differences, their mean, standard error, and a 95% interval."""
    tasks = sorted(before.keys() & after.keys())
    diffs = {task: after[task] - before[task] for task in tasks}
    if len(diffs) < 2:
        raise SystemExit(f"{len(diffs)} task(s) scored in both files. A paired interval needs at least 2.")
    centre = mean(diffs.values())
    se = stdev(diffs.values()) / sqrt(len(diffs))
    crit = t_critical_95(len(diffs)) if z is None else z
    return diffs, centre, se, (centre - crit * se, centre + crit * se)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("wilson", help="interval for a measured pass count")
    w.add_argument("passes", type=int)
    w.add_argument("attempts", type=int)
    r = sub.add_parser("rule", help="how often a kill or keep rule fires at each true rate")
    r.add_argument("--attempts", type=int, required=True)
    group = r.add_mutually_exclusive_group(required=True)
    group.add_argument("--kill-at", type=int, help="kill when passes reach this count")
    group.add_argument("--keep", type=int, nargs=2, metavar=("LOW", "HIGH"), help="keep when passes land in this range")
    r.add_argument("--rates", type=float, nargs="+", default=RATES)
    s = sub.add_parser("set", help="per-model pass rate over tasks, clustered by task, with pass@k and pass^k")
    s.add_argument("summary", help="a summarize_runs.py --json file")
    p = sub.add_parser("paired", help="interval of the per-task difference between two summarize_runs.py --json files")
    p.add_argument("before")
    p.add_argument("after")
    args = parser.parse_args(argv)

    if args.command == "paired":
        before, after = pass_rates(args.before), pass_rates(args.after)
        diffs, centre, se, (low, high) = paired(before, after)
        for task, diff in diffs.items():
            print(f"{task}\t{before[task]:.0%} to {after[task]:.0%}\t{diff:+.0%}")
        unpaired = sorted(before.keys() ^ after.keys())
        if unpaired:
            print(f"left out, scored in only one file: {', '.join(unpaired)}")
        print(f"{len(diffs)} tasks, mean difference {centre:+.1%}, SE {se:.1%}, 95% interval {low:+.1%} to {high:+.1%}")
        return

    if args.command == "set":
        by_model = {}
        for row in json.loads(Path(args.summary).read_text()):
            if row["scored"]:
                by_model.setdefault(row["model"], []).append(row)
        for model, rows in sorted(by_model.items()):
            if len(rows) < 2:
                raise SystemExit(f"{model} has {len(rows)} scored task. A set interval needs at least 2. Use wilson.")
            if len({r["scored"] for r in rows}) > 1:
                raise SystemExit(f"{model} has unequal runs per task. Rerun summarize_runs.py with --first N.")
            rates = [r["passes"] / r["scored"] for r in rows]
            centre, se, k = mean(rates), stdev(rates) / sqrt(len(rates)), rows[0]["scored"]
            crit = t_critical_95(len(rows))
            print(
                f"{model}\t{len(rows)} tasks\tmean pass rate {centre:.1%}, SE {se:.1%}, 95% interval "
                f"{max(0, centre - crit * se):.1%} to {min(1, centre + crit * se):.1%}\t"
                f"pass@{k} {sum(r['passes'] > 0 for r in rows)} of {len(rows)}\t"
                f"pass^{k} {sum(r['passes'] == r['scored'] for r in rows)} of {len(rows)}"
            )
        return

    if args.command == "wilson":
        low, high = wilson(args.passes, args.attempts)
        print(f"{args.passes} of {args.attempts}: {low:.0%} to {high:.0%}")
        return
    if args.kill_at is not None:
        label, low, high = "killed", args.kill_at, args.attempts
    else:
        label, (low, high) = "kept", args.keep
    for rate in args.rates:
        print(f"true rate {rate:.0%}: {label} {chance(rate, args.attempts, low, high):.1%}")


if __name__ == "__main__":
    main()
