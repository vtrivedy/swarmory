# Eval factory: four Harbor tasks that test root-cause debugging

## Goal

Build four Harbor tasks, each in its own folder under `tasks/`, that test this capability: **an agent finds the root
cause of a failure when a second explanation fits the evidence almost as well.** Each task must separate a solver that
has the capability from one that doesn't, and must test something the other tasks don't.

To build evals for another capability, change the title, the bold line above, and the number of tasks.

## How to build them

Follow `.agents/skills/eval-factory/SKILL.md` for each task, phase by phase. Copy its `templates/task/` to start a task
in `tasks/<task-name>/`, and keep the task's idea card, reviews, and report in `work/<task-name>/`.

Run Harbor through swarmory jobs, as `.agents/skills/running-harbor-jobs/SKILL.md` says. You hold no Modal token or
model key: a job worker runs Harbor on your commit and posts the result on the issue you name. Where the eval-factory
skill shows a `harbor run` or `verify_task.py free` command, use the job instead.

`create-task` and `rewardkit` are Harbor's own skills, for reference on the task format and on Reward Kit verifiers.

## How to check them

- Each task's own tests pass: `cd tasks/<name> && harbor-python -m pytest tests -q -p no:cacheprovider`.
- `swarmory job harbor tasks/<name> --agent oracle --to <its issue>` passes, and `--agent nop` fails.
- Models run as jobs too, such as `--agent claude-code --model claude-opus-5-5 --attempts 5 --to <its issue>`.

A task is done when it is on main, it passed every phase gate in the eval-factory skill through Phase 5, and someone
other than its builder tried to break it. The strongest model you can run must fail some runs, for the reason the
task's claim names. A task every model passes is a draft: make it harder and log each version in its idea card.

## How the work divides

Each task is one issue with one builder. Reviewing is separate work: critic, reader, auditor, and hacker each take a
task they didn't build, using the briefs in the eval-factory skill's `templates/`, and report on the task's issue.
