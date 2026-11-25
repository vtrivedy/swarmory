---
name: eval-factory
description: Build eval tasks for models and agents in Harbor format, from the first idea to a measured task. Use when you brainstorm an eval, build a task's environment and verifier, verify a task with models and reviewers, repair a broken task, make a task harder, or report eval results.
---

# Eval factory

You build eval tasks in Harbor format. A finished task passes when the solver has the capability the task names, and
fails when it lacks it. Follow the map below in order. Each step says what to do, which file to use, and when the step
is done. The refund task, an invented example, shows the result of each step.

Run models whenever a step needs them, within any budget the user set. If the user asked you to work autonomously,
don't stop to ask.

## The map

| Step | Do | Use | Done when |
|---|---|---|---|
| [1. Claim](#1-write-the-claim) | Say what the task measures and how a pass looks | [idea-card.md](templates/idea-card.md), [critic-brief.md](templates/critic-brief.md) | The critic answers `BUILD` |
| [2. Environment](#2-build-the-environment) | Build the data, tools, and services around a known answer | [templates/task/](templates/task), [hardening.md](references/hardening.md) | A script derives the key from the environment |
| [3. Verifier](#3-write-the-verifier) | Write the checks that decide pass or fail | [verifiers.md](references/verifiers.md) | Every stored case scores what it should |
| [4. Instruction](#4-write-the-instruction) | Tell the solver the outcome you check | [reader-brief.md](templates/reader-brief.md) | The reader answers `PASS` |
| [5. Free checks](#5-run-the-free-checks) | Prove the task works with no model | [verify_task.py](scripts/verify_task.py), [harbor.md](references/harbor.md) | `free` exits 0 with no `skip` row |
| [6. Review](#6-send-the-auditor-and-the-hacker) | Have others solve it blind and try to break it | [auditor-brief.md](templates/auditor-brief.md), [hacker-brief.md](templates/hacker-brief.md) | `PASS` and `NONE_FOUND` |
| [7. Model runs](#7-run-models-and-read-every-run) | Run models and name the cause of every failure | [harbor.md](references/harbor.md), [summarize_runs.py](scripts/summarize_runs.py) | The target model fails some runs for the claim's reason |
| [8. Repair](#8-repair) | Fix what steps 5 to 7 found | The file the cause names | Back to step 5 |
| [9. Harder](#9-make-it-harder) | Only when the target passes every run | Your idea card's knob | Back to step 5 |
| [10. Report](#10-measure-and-report) | Freeze the task and measure it | [probe_odds.py](scripts/probe_odds.py), [harbor.md](references/harbor.md#write-the-report) | The report has every item |

For an existing task, start at the step that matches the job. If every run scores 0 or every run scores 1, start with
[the all-0 or all-1 checklist](references/harbor.md#all-0-or-all-1).

## Files

| File | What it holds |
|---|---|
| [templates/task/](templates/task) | A complete Harbor task to copy: `task.toml`, `instruction.md`, `environment/`, `solution/solve.sh`, `tests/` with the verifier and stored cases, and `preflight/` |
| [templates/idea-card.md](templates/idea-card.md) | The task's record, from claim to frozen version |
| [templates/](templates/) `*-brief.md` | The prompts for the critic, reader, auditor, and hacker |
| [references/verifiers.md](references/verifiers.md) | Verifier code to copy for each kind of check |
| [references/hardening.md](references/hardening.md) | Isolation settings, the preflight, and known exploits with their defenses |
| [references/harbor.md](references/harbor.md) | Every command, known traps, the all-0 or all-1 checklist, and the report format |
| [references/benchmarks.md](references/benchmarks.md) | Published benchmarks whose designs are worth copying |
| [scripts/verify_task.py](scripts/verify_task.py) | `doctor` checks setup, `free` runs every check that needs no model, `cleanup` stops leaked sandboxes |
| [scripts/lint_task.py](scripts/lint_task.py) | Checks isolation settings and the reference case without building |
| [scripts/summarize_runs.py](scripts/summarize_runs.py) | Counts each run as a pass, fail, refusal, or infrastructure failure |
| [scripts/probe_odds.py](scripts/probe_odds.py) | Intervals for one task, a set of tasks, or two versions, and the odds of a keep rule |
| [scripts/preflight_agent.py](scripts/preflight_agent.py) | The Harbor agent that `verify_task.py` uses to run the preflight. You never run it directly. |

## Reviewers

You build the task. Every reviewer is a new agent that never sees your context, on a different model family when one
is available. Give it its brief with every `<...>` filled, the task folder, and this skill's folder path. It writes
its report to `tasks/<task-name>/reviews/<role>-v<N>.md` and ends with one verdict line.

## 1. Write the claim

1. Copy [idea-card.md](templates/idea-card.md) to `tasks/<task-name>/idea-card.md` and fill each field.
2. Name the target model and harness, one behavior a run shows, and the condition that makes it hard. If the user
   named no model, use the strongest one the endpoint lists.
3. Write what a run with the capability leaves behind, and what a run without it leaves. A check must be able to tell
   them apart, so the judgment has to land in a file, a record, or a field.
4. Name the cheapest way to pass without the capability. If one blanket policy passes, such as "always retry", plan a
   second task folder where that policy fails.
5. Search [benchmarks.md](references/benchmarks.md) and `harbor datasets list` for a close task. Start from it if one
   exists.
6. Send the [critic](templates/critic-brief.md). On `REVISE`, fix the named fields and send a new critic. On `DROP`,
   pick another idea. After three rounds without `BUILD`, stop and report.

Reject an idea that one script solves without the environment.

> **Refund task.** Claim: when a refund call times out, the support agent makes sure exactly one refund happens before
> it tells the customer. Without the capability, the payment ledger shows two refunds. Shortcut: "never retry", so a
> second folder where the first call times out before it commits makes "never retry" fail. Knob: how long the refund's
> status reads "pending".

## 2. Build the environment

1. Copy [templates/task/](templates/task) to `tasks/<task-name>/`.
2. Decide the answer first. Generate the data from it with a seeded script kept in `tests/`, so the solver's image
   never holds the answer. Read a sample of what the script produced.
3. Make every fact the verifier checks readable by the solver.
4. Add the defects real data has, such as near-duplicate records, mixed units, and conflicting policies.
5. Install the tools a practitioner would use, so no run fails on setup.
6. Run each mock service as its own service in `environment/docker-compose.yaml`. The service owns its state and logs
   every call. List those files in `artifacts` in `task.toml`.
7. Keep the isolation settings that the template already has. [hardening.md](references/hardening.md) explains each.
8. Add a line to `preflight/preflight_checks.sh` for each tool the task needs.

> **Refund task.** A mock payments API logs every refund to a ledger and every call to a call log. Order 1182 was
> charged twice for a $42.10 item. In `refund-committed`, the first refund call commits and then times out. In
> `refund-uncommitted`, it times out before it commits.

## 3. Write the verifier

1. Write the rule as one sentence: "Pass if and only if <an outcome you can observe>".
2. Check the final state, not the steps the solver took.
3. Use code for every check that code can decide. Use a decision call for a narrow yes-or-no question about language,
   and a rubric judge only for open output. Give either one the evidence.
4. Parse each value and compare it exactly. Accept harmless variations in format and order.
5. Check that nothing else changed.
6. Copy the code for each check from [verifiers.md](references/verifiers.md).
7. Store these cases in `tests/cases/<name>/`, each with an `expected.json`, and run `uvx pytest tests`:

| Case | Holds | `expected.json` |
|---|---|---|
| `reference` | What `solution/solve.sh` writes | `{"reward": 1.0}` |
| `alternative` | Another valid answer | `{"reward": 1.0}` |
| `empty` | Nothing | `{"reward": 0.0}` |
| `garbage` | Malformed output | `{"reward": 0.0, "failed_check": "<id>"}` |
| `exploit-<name>` | The cheapest shortcut, written as code | `{"reward": 0.0, "reward_without_hardening": 1.0}` |
| `near-miss` | A plausible wrong answer | `{"reward": 0.0, "failed_check": "<id>", "partial_score": <fraction>}` |
| `side-effect` | A correct answer plus a change it shouldn't make | `{"reward": 0.0, "failed_check": "<id>"}` |

`reward_without_hardening` is the score with the defense turned off. A 1 there proves the hole was real.

> **Refund task.** Pass if and only if the ledger has exactly one new refund, of $42.10 on order 1182, nothing else
> changed, and the reply states that the refund was issued, its amount, and when it arrives.

## 4. Write the instruction

1. Trace every check to an instruction sentence, and give every stated requirement a check. Leave a rule out only when
   finding it is the capability.
2. State the outcome, the output path, the format, and the limits. Never state the method.
3. Make every fact true and every unit explicit.
4. Send the [reader](templates/reader-brief.md) for its first section only.

> **Refund task.** "Order 1182 was charged twice for the same $42.10 item. Refund the duplicate charge with your
> payments tools. Then write the customer a reply in `/app/output/reply.txt` that says the refund was issued, states
> its amount, and says when it will arrive. Don't change any other order. Only a fully correct result passes." The
> instruction doesn't mention timeouts, because noticing one is the capability.

## 5. Run the free checks

1. Commit the task on a work branch.
2. Run [lint_task.py](scripts/lint_task.py) and then [verify_task.py](scripts/verify_task.py) `free`. The commands are
   in [harbor.md](references/harbor.md#check-the-task-with-no-model).
3. Fix every failed row. A failed row is a defect in the task, not in the check.

## 6. Send the auditor and the hacker

1. Build the image and give each reviewer a shell command, as
   [harbor.md](references/harbor.md#open-a-shell-in-the-solvers-image) shows.
2. Send the [auditor](templates/auditor-brief.md). Score its answer with the verifier. It must score 1.
3. Send the [hacker](templates/hacker-brief.md). Each break it finds becomes a stored case or a preflight line.

> **Refund task.** The hacker refunded once, then deleted the duplicate charge from the order database. Every check
> passed, because no check read that database.

## 7. Run models and read every run

1. Run a cheap model first, to find bugs in the task.
2. Run two or three models of different strength. A failure they share points to the task. A failure unique to one
   model points to that model.
3. Keep one strong model out of every run until step 10.
4. Count the runs with [summarize_runs.py](scripts/summarize_runs.py).
5. Read every pass by the target model, shortest first, against the
   [known exploits](references/hardening.md#known-exploits).
6. For each failure, quote the first wrong step and name its cause:

| Cause | Next |
|---|---|
| Capability | Nothing. This failure is the measurement. |
| Missing or misleading information | Fix the instruction or the environment. |
| Tool, service, or setup | Fix the environment. |
| Verifier wrong in either direction | Fix the verifier, then regrade. |
| Exploit or leak | Close it and store the run as `exploit-<name>`. |
| Infrastructure | Rerun it, unscored. |

Also count the runs that stop before checking their work, and the runs that claim success on a wrong result.

> **Refund task.** The target model failed one of two runs. It called `POST /refunds`, got a 504, and called it again
> without checking the status. The cause is the capability.

## 8. Repair

1. Fix the layer that the cause names. Never make a task harder to hide a defect.
2. Close exploits in the environment. A sentence in the instruction doesn't stop a solver.
3. Store each finding as a case. A break whose output looks correct, such as reading the key, becomes a preflight line
   instead.
4. Make the smallest fix, on the last reviewed version. After a change to the verifier alone, regrade the stored runs
   instead of running models again.
5. Log the error in the idea card.
6. Go back to step 5, and send the hacker again. After three rounds on one defect, redesign the task or drop it.

> **Refund task.** The service now owns the order database, the verifier gains a `charges_unchanged` check, and the
> hacker's run becomes the stored case `exploit-delete-charge`.

## 9. Make it harder

Do this only when the target model passes every run.

1. Turn the knob one step. Ask for more judgment, such as a closer decoy, a stale fact to refetch, or a check before
   an irreversible action. Never add only size, noise, or a tighter clock.
2. Keep the verifier the same. If the harder version needs a new claim, it is a new task.
3. Keep the easier version. A task that no model passes needs an easier version that some model passes, or a wrong
   key looks like a hard task.

> **Refund task.** Easier: the status reads "pending" for 0 seconds. Base: 5 seconds. Harder: 10 seconds, so an agent
> that retries on "pending" refunds twice.

## 10. Measure and report

1. Freeze the task: commit it, pin its images and harness, and stop editing.
2. Run each model fresh, at least 10 runs per task.
3. Count the runs with [summarize_runs.py](scripts/summarize_runs.py) `--first 10 --json`.
4. Compute the intervals with [probe_odds.py](scripts/probe_odds.py): `wilson` for one task, `set` for several, and
   `paired` to compare two models or two versions.
5. Write the report in the format in [harbor.md](references/harbor.md#write-the-report): the setup, the pass rate with
   its interval, pass@k and pass^k, the partial score, cost, early stops, false success claims, the empty answer's
   score, and what the task leaves untested.

Once the target models pass every run, keep the task as a regression check, not a capability measure.
