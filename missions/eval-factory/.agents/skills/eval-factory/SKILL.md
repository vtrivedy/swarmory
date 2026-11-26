---
name: eval-factory
description: Build eval tasks in Harbor format from idea to measured score. A pass means the solver has the capability, and a fail means it lacks it. Use when you design, build, verify, repair, or measure a task.
---

# Eval factory

Build eval tasks in Harbor format autonomously. Run reviewers and models whenever a phase needs them, within any budget the user sets. Do not pause for approval.

A pass must mean the solver has the capability. A fail must mean the solver lacks the capability. Keep Harbor task files in `tasks/<task-name>/` and working records in `work/<task-name>/`. That split keeps idea cards, reviews, labels, and reports out of Harbor uploads and out of clean git checks on the task folder.

Four references hold commands, code, and Harbor rules.

- [references/environment.md](references/environment.md) covers task layout, synthetic data, isolation, mock services, preflight checks, leak searches, and known exploits.
- [references/verifiers.md](references/verifiers.md) covers deterministic verifier code, error handling, stored cases, and grade records.
- [references/judges.md](references/judges.md) covers model-based grading, isolated binary rubric judges, and three-way judge calibration.
- [references/harbor.md](references/harbor.md) covers tool checks, reviewer dispatch, free checks, solver shells, model runs, reruns, freezing, counting, and reporting.

Before Phase 1, [check the local tools and model endpoint](references/harbor.md#check-prerequisites). If a prerequisite fails, stop and report the failure.

## How an eval fails

A score is valid only when the claim, the instruction, and the verifier agree. Otherwise the task fails in one of three ways.

| Failure | Meaning | Common causes |
|---|---|---|
| False pass | A solver without the capability passes | Shortcut, readable key, weak check, missed collateral change |
| False fail | A solver with the capability fails | Unstated rule, brittle check, wrong key, broken tool |
| Noise | The score moves without a capability change | Too few runs, reused tuning runs, uncalibrated judge, unrecorded runtime change |

## Workflow map

Follow the phases in order.

For an existing task, write its idea card and port it to the template layout if needed. Then start at Phase 2 verifier checks. If every run scores 0 or 1, start at [all-0 or all-1 triage](references/harbor.md#triage-all-0-or-all-1).

| Phase | Output | Gate | On failure |
|---|---|---|---|
| [1. Brainstorm](#phase-1-brainstorm) | `work/<task-name>/idea-card.md` | Critic answers `BUILD` | Revise card or pick a new idea |
| [2. Build](#phase-2-build) | `tasks/<task-name>/` with stored cases | Reader answers `PASS`, cases and lint pass | Fix owning Build sub-stage |
| [3. Verify](#phase-3-verify) | Free checks, reviews, labeled runs | `free` exits 0, no leaks, auditor answers `PASS` and scores 1, hacker answers `NONE_FOUND`, only capability fails | Go to Phase 4 |
| [4. Repair](#phase-4-repair) | Smallest fix, regression case, new version | Re-pass from changed sub-stage through Phase 3 | Drop after three failed rounds |
| [5. Measure](#phase-5-measure) | Frozen commit and `work/<task-name>/report.md` | Frozen `free` passes and report is complete | Fix missing setup or rerun outages |

## Independent reviewers

You know the hidden intent, so you miss gaps in your own task. Run each reviewer on another model family when available, or in a fresh session with no shared context. [Dispatch each brief](references/harbor.md#dispatch-a-reviewer) with every placeholder filled, and read the verdict on its last line.

Start the card at `v1`. Whenever you edit a file that a reviewer or model read, start a new version `v<N>`. Commit `tasks/<task-name>/` before every check, review, or model run, because Harbor uploads files from disk and only a commit ties a result to a SHA. Record the commit SHA in `work/<task-name>/idea-card.md`.

## Phase 1. Brainstorm

Produce `work/<task-name>/idea-card.md`. Exit when the critic answers `BUILD`.

1. Copy [templates/idea-card.md](templates/idea-card.md), the task record, to `work/<task-name>/idea-card.md`.
2. Ground the task in real work or an observed failure. Invented tasks test failures agents may not have.
3. Classify the task as `capability` or `regression`, and state a narrow claim and what it leaves untested. A pass rate near 0 or 1 cannot show progress.
4. Require the capability to change saved state. Solvers often claim success without acting, so a script must read the difference in files or service records.
5. Plan paired control and abstain variants. Tasks with only positive cases reward blanket policies. Pair every abstain case with an answerable case so an always-refuse solver cannot pass.
6. Pick work that is hard to do, cheap to check, novel, and unsaturated. A verifier as hard as the task inherits solver errors. A public task can be passed from memory, and a task every model passes cannot rank models.
7. Reject tasks that need interactive prompts, GUI clicks outside a GUI claim, or triggers for unrelated safety refusals. Those failures block non-interactive agents for reasons outside the claim.
8. Never turn a judgment task into a naming checklist or give the solver a helper that mirrors the verifier score. Either lets a solver pass by matching names or tuning to the score.
9. Send [templates/critic-brief.md](templates/critic-brief.md), the idea-review prompt, to a fresh critic. On `REVISE`, update the named fields and send a new critic. On `DROP`, choose another idea. After three rounds without `BUILD`, stop and report.

## Phase 2. Build

Produce a complete Harbor task in `tasks/<task-name>/`. Exit when the reader answers `PASS` on the instruction, stored cases pass, and the linter exits 0.

### Environment

1. Copy [templates/task/](templates/task), the starter Harbor task, to `tasks/<task-name>/`. Replace every starter placeholder as [lay out a task folder](references/environment.md#lay-out-a-task-folder) specifies.
2. Decide the answer first, then write a seeded generator in `tests/` that builds state forward. A key derived after the fact can be wrong, and hand-patched state can create a second valid answer.
3. For tasks with prose artifacts, [build structured truth in seeded code first](references/environment.md#build-hybrid-synthetic-data). Generate prose only from allowed facts, and never use unverified model output as ground truth. Free-written prose invents facts that contradict the key.
4. Inspect generated sample output before moving on. A generator bug looks identical to a hard task later.
5. Make every graded fact readable to the solver. A fact stored only in the key reads as a missing capability.
6. Pin every dependency and reset initial state per trial. Unpinned packages cause drift, and leftover files leak side effects across trials.
7. Keep real domain friction and decoys with ruling-out evidence, not artificial scale. Unruled decoys create second valid answers, and sheer size tests patience rather than judgment.
8. Keep [solver and verifier isolation](references/environment.md#isolate-the-solver-from-the-verifier). Shared containers, root access, or open networks let solvers read keys or forge rewards.
9. When the task needs a service, run it [as root with private state](references/environment.md#run-a-mock-service-the-solver-cannot-edit). Enforce domain rules in the service backend, and never branch backend behavior on task ID or expected answer. A solver that can write the service's state can fake the outcome without calling it.

### Verifier

Write the verifier before the instruction so the instruction derives from the checks. Copy patterns from [references/verifiers.md](references/verifiers.md) into `tests/verifier.py`.

1. Keep [the template interface](references/verifiers.md#keep-the-template-interface). Stored cases, the linter, and the run summarizer depend on `grade()`, `grade.json`, and `hardening=False`.
2. Grade saved final state and snapshot untouched collateral state. Checking only target fields passes solvers that cause harm elsewhere.
3. Decompose requirements into independent binary checks. Binary checks produce deterministic verdicts and expose partial progress without subjective rating scales.
4. Bind parsed values to roles, canonicalize unordered rows or keys, and match boundary operators and rounding to `instruction.md`. Rigid order checks reject valid work, while unbound values pass swapped answers.
5. Record method checks only as `diag_`. Grading trajectories rejects valid alternative solutions.
6. [Isolate submitted code](references/environment.md#isolate-submitted-code) outside the grader process. When tests are visible, [perturb hidden test inputs](references/environment.md#perturb-inputs-when-the-solver-sees-the-tests). Code inside the grader can forge its own exit code, and visible tests invite memorization.
7. Use code for counts, IDs, values, and order. When code cannot grade a requirement, follow [references/judges.md](references/judges.md) for isolated binary judges instead of generic metrics.
8. [Separate verifier errors from solver failures](references/verifiers.md#handle-errors-one-way). Bad solver output scores 0 on a named check, while a judge or tool failure exits nonzero with no reward.
9. Give every check an ID and a comment naming the shortcut or defect it blocks. An unnamed guard protects against nothing you have verified.

### Instruction

1. Edit `instruction.md` so every check maps to a sentence and every sentence maps to a check. An unstated rule fails competent solvers, and an unchecked rule is free to ignore.
2. State exact paths, formats, units, tolerances, tool or dialect versions, and limits from `task.toml`. State outcomes, never methods, because dictating steps lets solvers pass without the capability.
3. For coding tasks, declare exact editable files or allow scratch tests outside graded paths. Solvers must be able to test their own fix without failing scope checks.
4. Omit a rule only when discovering that rule is the capability. Ensure an action in the environment reveals it.
5. Send [templates/reader-brief.md](templates/reader-brief.md), the alignment-review prompt, with `<job folder>` set to `none`. On `REVISION_REQUIRED`, fix the named mismatches and send a new reader before writing stored cases.

### Reference solution and stored cases

1. Finish `solution/solve.sh` to perform the real work, then [run it in the built image](references/harbor.md#run-the-reference-solution). A verified reference execution catches impossible specs and broken tools before any model runs.
2. [Store bidirectional cases](references/verifiers.md#stored-cases) covering passing, alternative, empty, malformed, near-miss, side-effect, and shortcut outputs. A verifier tested only on passes never proves it rejects bad work.
3. Run `pytest` on `tests/`. When `--container-verifier` is required, run stored cases inside the verifier container. Run [scripts/lint_task.py](scripts/lint_task.py), the static isolation and format checker, on `tasks/<task-name>/`. Fix every failure before Phase 3.

## Phase 3. Verify

Confirm the task is solvable, sealed, and free of non-capability failures before measuring. Exit when free checks pass, no hidden files leak, the auditor scores 1, and the hacker finds no break. Every remaining model failure must be a capability failure.

1. Run [scripts/verify_task.py](scripts/verify_task.py), the task verification runner, with `doctor` and `free` in [harbor.md order](references/harbor.md#run-free-checks). `free` runs [scripts/preflight_agent.py](scripts/preflight_agent.py), the preflight Harbor agent, plus oracle and `nop` baselines. An oracle pass proves solvability, and a `nop` fail proves untouched state cannot pass.
2. Fix every failed or skipped row in `summary.md`.
3. [Search the built image for hidden files](references/environment.md#search-for-leaks). Preflight checks only `/tests` and `/solution`, so keys copied elsewhere pass `free`.
4. [Open a solver shell](references/harbor.md#open-a-solver-shell) and send [templates/auditor-brief.md](templates/auditor-brief.md), the blind-solve prompt. Score the auditor output. A wrong key looks identical to a hard task until an agent without the key solves it.
5. Open a separate solver shell and send [templates/hacker-brief.md](templates/hacker-brief.md), the adversarial audit prompt. Hostile testing catches loopholes before models exploit them. Send any auditor issue or hacker break to Phase 4.
6. [Run models](references/harbor.md#run-models) starting with a cheap model, then two or three models of varying strength including the target. A cheap model finds setup bugs before expensive runs. Hold out one strong non-target model until Phase 5 so you do not tune to it.
7. Summarize trials with [scripts/summarize_runs.py](scripts/summarize_runs.py), the trial accounting script. Read every passing trace, shortest and fastest first, against [known exploits](references/environment.md#known-exploits). Pass-rate ordering alone never proves validity, because fast passes and weak-model wins can hide shortcuts.
8. For each failure, quote the first upstream wrong step because downstream errors cascade from it. Classify the failure as `capability`, `information`, `setup`, `verifier`, `exploit`, or `infrastructure`. Rerun `infrastructure` unscored, and send every other non-capability cause to Phase 4.
9. If independent models fail alike or strong models score 0 across runs, suspect the task. [Regrade](references/harbor.md#regrade-without-model-calls) with a scratch verifier accepting their shared reading. If the regraded runs pass, send the reader with `<job folder>` set to the job and repair in Phase 4.
10. If the task uses a model judge, [run three-way judge calibration](references/judges.md#calibrate-a-judge) before Phase 5. Overall agreement hides one-sided errors on skewed outputs.
11. If the target model passes every run validly, stop before Phase 5. [Raise difficulty](#raise-difficulty) on a copy or keep the task as a regression check.
12. Once variant one exits Phase 3 cleanly, build and verify each planned control and abstain variant before Phase 5. Early defects multiply across copies.

## Phase 4. Repair

Fix one defect cleanly without drifting the task. Exit when every gate from the earliest changed Build sub-stage through Phase 3 passes again.

| Failure cause | Owning layer | Repair action |
|---|---|---|
| `information` | Phase 2 Instruction | Clarify wording, format, unit, version, or discoverable rule |
| `setup` or wrong key | Phase 2 Environment | Fix generator, dependencies, tools, or service setup |
| `verifier` | Phase 2 Verifier | Fix parsing, normalization, tolerance, or scope check |
| `exploit` | Phase 2 Environment or Verifier | Close with isolation, preflight line, or verifier check |

1. Make the smallest change in the owning layer. Fixing a prompt to hide an exploit leaves the hole open, and rewriting the whole task drifts its claim.
2. Store every defect or exploit as a regression case in `tests/cases/`. When an exploit produces valid output, add a preflight check instead. Without a stored check, later edits reopen closed flaws.
3. Log the defect, discovery method, affected versions, and score comparability in `work/<task-name>/idea-card.md`. Changing preinstalled packages changes the evaluation condition. Whenever grading logic changes, bump `VERSION` in `tests/verifier.py`, because two verifier versions cannot share a table.
4. Re-pass every gate from the earliest changed Build sub-stage through Phase 3. If only the verifier changed, [regrade stored runs](references/harbor.md#regrade-without-model-calls) instead of rerunning models. After three failed rounds on one defect, restart at Phase 1 or drop the task and record why.

## Phase 5. Measure

Produce a frozen commit and `work/<task-name>/report.md`. Exit when frozen `free` passes, fresh trials finish, and the report contains every required item.

1. [Freeze the task](references/harbor.md#freeze-the-task) with pinned image digests, exact versions, a clean `free` pass, and a commit. Tuning runs overstate scores, so measure only on fresh frozen runs.
2. Write the keep rule in `work/<task-name>/idea-card.md` as a pass range before measuring. Check its error rates with [scripts/probe_odds.py](scripts/probe_odds.py), the statistical interval and rule calculator. Picking thresholds after seeing scores lets noise select the task.
3. Run at least 10 fresh trials per task for each model, including the held-out model. [Rerun infrastructure failures](references/harbor.md#rerun-infrastructure-failures) unscored, because counting outages as zeros understates capability.
4. Count the first clean runs with `summarize_runs.py` and `probe_odds.py`, then [write `work/<task-name>/report.md`](references/harbor.md#count-and-report). Scores measure the model and agent CLI pair together. Decide from confidence intervals, because 10 runs leave a wide interval.
5. Apply the keep rule. If scores sit above the range, keep the task as a regression check. Then [raise difficulty](#raise-difficulty) on a copy. If scores sit below the range, weaken one condition on a copy and pass Phases 2 and 3.

### Raise difficulty

Raise difficulty only on a fair task that the target passes cleanly. Never raise difficulty to hide a defect. Copy the task folder and keep the easier task, because a task no model passes makes a wrong key look hard.

In the generator, harden one domain condition such as a closer decoy or a stale record. Keep the verifier unchanged. If a change requires a new claim, start a new task at Phase 1. Regenerate data and key, update `solve.sh` and stored cases, and pass Phases 2 and 3.
