---
name: running-harbor-jobs
description: How a swarmory run's Harbor jobs flow, from `swarmory job harbor` to the result message, and how to use them well. Use before you queue, cancel or read a job.
---

# Running Harbor jobs

A run started with `--jobs` runs Harbor for you. You hold no Modal token and no model key.

## What happens to a job

1. `swarmory job harbor PATH --agent A` refuses a folder with uncommitted changes. It pushes your HEAD and sends the
   commit and the path.
2. The server checks the job. The agent is oracle, nop, claude-code or codex, and the last two need `--model`. Every
   task.toml must be sealed: `[environment] network_mode = "no-network"`, and no `${...}` value in an env table. It
   records the folder's git tree, so the result names exactly what ran.
3. Within a few seconds a job worker starts in its own sandbox. It fetches the committed folder and runs Harbor on
   it. Jobs don't wait for each other.
4. Harbor runs each trial in its own sandbox: on Modal for a Modal server, in Docker for a local one. A model
   trial reaches only the run's model gateway, billed to the job, and stops at `--budget USD`. A trial whose
   sandbox did not start in time (`EnvironmentStartTimeoutError`) is run once more by Harbor.
5. The worker ends any of the job's sandboxes that Harbor left, or the server does when the worker is lost.
6. The result comes as a message in `--to` (default `general`) that @mentions you, with Harbor's output attached:
   "P passed, F failed, E errored of N", then the first line of each errored trial's error. `swarmory get FILE`
   downloads the output.

## Folder jobs and arm jobs

PATH is one task folder, or a folder whose subfolders are tasks. A folder job runs every task in one job, and its
trials run at the same time: attempts times tasks, at most 8 at once. `--attempts` takes any whole number from 1.
Check oracle and nop on all arms of a task with one folder job each, not one job per arm. Use an arm job when only
that arm changed.

## Commands

```
swarmory job harbor PATH --agent A [--model M] [--attempts N] [--budget USD] [--hours H] [--to WHERE]
swarmory job list [--mine]     the run's jobs, newest first
swarmory job show ID           each trial's reward and error
swarmory job cancel ID         only a job you queued
swarmory results PATH          every job on this folder's current content
```

## Read the result

- Read the errors before the rewards. An errored trial is not a wrong answer: it counts under "errored", not
  "failed". `swarmory job show ID` lists each trial's reward and error.
- `EnvironmentStartTimeoutError` that survived Harbor's retry, or "Harbor exited N with no trial results", is
  infrastructure. Rerun that task once, alone. If it fails again, say so in the issue.
- `ImageBuildError` is a bug in the task's Dockerfile. Fix the task. A rerun fails the same way.

## Share the run

- The server refuses to cancel another member's job. Ask its owner.
- Cancel your own jobs on a commit you replaced. A canceled job's trials can start no new model call.
- After you queue, go on with other work. The result is a message that @mentions you, so your next turn shows it.
  Don't poll `job list` or sleep in a loop.
