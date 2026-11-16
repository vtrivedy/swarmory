# Eval factory: four Harbor tasks that test root-cause debugging

## Goal

Build four Harbor tasks, each in its own folder under `tasks/`, that test this capability: **an agent finds the root
cause of a failure when a second explanation fits the evidence almost as well.** Each task must separate a solver
that has the capability from one that doesn't, and must test something the other tasks don't.

To build evals for another capability, change the title, the bold line above, and the number of tasks in the first
sentence.

## How to build them

Read `skills/eval-factory/SKILL.md` first and build its map, as it says: list every reference's sections, then read
each reference in full when you reach the decision it covers. Copy `skills/eval-factory/templates/task/` to start each
task.

## How to check them

- Each task's own tests pass: `cd tasks/<name> && harbor-python -m pytest tests -q -p no:cacheprovider`. The tests
  must include the stored cases `empty` (scores 0) and `reference` (scores 1), as the template's do.
- Commit each task, then run it with `swarmory job harbor tasks/<name> --agent A --to <its issue>`. A job
  worker runs Harbor on that commit for you and posts the result on the issue, with the job's artifacts attached:
  each trial's reward, logs and trajectory. Start with `--agent oracle`, which must pass, and `--agent nop`, which
  must fail. Then run models, such as `--agent claude-code --model claude-opus-5-5 --attempts 5` or `--agent codex
  --model gpt-6.1-sol`. `swarmory results tasks/<name>` lists the finished jobs on the folder as it is now. You hold no
  Modal token or model key: the worker holds the token, and trials call models through the server.

A task is done when it is on main, it meets "A task is ready when" in `skills/eval-factory/SKILL.md`, and someone
other than its builder has tried to break it. One of those conditions is that the strongest model you can run fails
some runs, for the reason the task's claim names. A task every model passes is a draft. Use the time you have left to
make it harder, and record each version in its idea card.

## How the work divides

Each task is one issue with one builder. Breaking tasks is its own work: take a task you didn't build and try to make
it pass without the capability, fail a valid answer, or break its environment. Report each break on the task's issue.
