# Eval factory: four Harbor tasks that test root-cause debugging

## Goal

Build four Harbor tasks, each in its own folder under `tasks/`, that test this capability: **an agent finds the root
cause of a failure when a second explanation fits the evidence almost as well.** Each task must separate a solver that
has the capability from one that doesn't, and must test something the other tasks don't. A task's control and abstain
variants count as part of that task and share its issue.

## How to build them

Follow `.agents/skills/eval-factory/SKILL.md` for each task, phase by phase.

Run Harbor through swarmory jobs, as `.agents/skills/running-harbor-jobs/SKILL.md` says. Run one job per variant
folder. Steps in the eval-factory skill that need Docker, Modal, or a model key change in this mission.

| Skill step | In this mission |
|---|---|
| Check prerequisites | Skip it. The first oracle job is the check. |
| Reference solution and `verify_task.py free` | Task tests, `lint_task.py`, then oracle and nop jobs. Take `tests/cases/reference/` from the oracle job's artifacts. |
| `harbor run` | `swarmory job harbor PATH --agent A --model M --attempts N`, with any Harbor agent or a harness folder, and Harbor's options such as `--ek keepalive=...` for mock services |
| Rerun infrastructure failures | Rerun that task alone as a new job. |
| Regrade | Run `tests/verifier.py` on each trial's artifacts from `swarmory get`. |
| Preflight and leak search | Commit an executable `preflight/run` that runs `preflight_checks.sh` and the leak `find`. Run `--agent tasks/<name>/preflight --model M` and read `run.log`. This route is untested. |
| Solver shell | The auditor and the hacker read the task's committed files. They test behavior in the image with committed harness jobs, `--agent <folder> --model M`. |
| Model judge | Not available, because a job refuses a verifier with network. Grade with code. |
| Freeze digests | Pin versions in the Dockerfiles and skip digests. |

`create-task` and `rewardkit` are Harbor's own skills, for reference on the task format and on Reward Kit verifiers.

## How to check them

- Each task's own tests pass: `cd tasks/<name> && harbor-python -m pytest tests -q -p no:cacheprovider`.
- `swarmory job harbor tasks/<name> --agent oracle --to <its issue>` passes, and `--agent nop` fails.
- Models run as jobs too, such as `--agent claude-code --model claude-opus-5-5 --attempts 10 --to <its issue>`.

A task is done when it is on main and passed every phase gate in the eval-factory skill through Phase 5. The target
model must fail some runs, for the reason the claim names. If it passes every run, follow the skill's raise difficulty
section.

## How the work divides

Each task is one issue with one builder. Reviewing is separate work: critic, reader, auditor, and hacker each take a
task they didn't build, using the briefs in the eval-factory skill's `templates/`. The reviewer fills the brief, writes
the report to `work/<task-name>/reviews/<role>-<version>.md`, puts any auditor answer in
`work/<task-name>/reviews/auditor-<version>-answer/output/`, and posts the verdict line on the task's issue. The builder
skips the skill's reviewer dispatch.
