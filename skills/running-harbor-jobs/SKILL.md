---
name: running-harbor-jobs
description: How a swarmory run's Harbor jobs flow, from a finished task folder to the result on the board, and how to use them well. Use before you queue, cancel or read a job, and before you reach for `harbor run`, `docker` or `modal`.
---

# Running Harbor jobs

A run started with `--jobs` runs Harbor for you. You never run `harbor`, `docker` or `modal` yourself, except
`harbor datasets list`. You hold no Modal token and no model key. Where another skill, such as create-task, rewardkit
or eval-factory, shows a `harbor run`, `harbor task init` or `docker` command, this skill takes precedence in a
swarmory run.

## The job flow

1. Build a finished task folder: `task.toml`, `instruction.md`, `environment/`, `tests/` and `solution/`. Write the
   files by hand or copy a template. Don't run `harbor task init`.
2. Seal it. `[environment]` and any `[verifier.environment]` have `network_mode = "no-network"` and no other network
   field. `[agent]` and `[verifier]` set no network field. No `env` table holds a `${...}` value. A task that needs
   the network, or a verifier that calls a model, can't run as a swarmory job.
3. Commit it. `swarmory job harbor` pushes your HEAD commit to a job ref on the server, not to main, so the job
   needs no push of its own. Push your work to main as usual, so the team has it.
4. Run `swarmory job harbor PATH --agent A [--model M] [--attempts N] [--env docker|modal] --to WHERE`.
5. The server checks the job. The agent is oracle, nop, claude-code or codex, or a custom harness folder (see
   below); all but oracle and nop need `--model`. Every task.toml must be sealed, and the folder may hold no
   `docker-compose.yaml`, in `environment/` or `tests/`, or the job is refused with the reason. The server records
   the folder's git tree, and the harness folder's, so the result names exactly what ran.
6. Within a few seconds a job worker starts in its own sandbox. It fetches the committed folder and runs Harbor on
   it. Jobs don't wait for each other.
7. Harbor runs each trial in its own sandbox: on Modal for a deployed server, in Docker for a local one, unless
   `--env` says otherwise. A model trial reaches only the run's model gateway, billed to the job, and stops at
   `--budget USD`. A trial whose sandbox did not start in time (`EnvironmentStartTimeoutError`) is run once more.
8. The worker ends any of the job's sandboxes that Harbor left, or the server does when the worker is lost.
9. The result comes as a message in `--to` (default `general`) that @mentions you: "P passed, F failed, E errored
   of N", then the first line of each errored trial's error. Harbor's output is attached as `job-<id>.tar.gz`.
   `swarmory get FILE` downloads it, where FILE is the attachment's id in the message (such as `f3`). Inside,
   `job-<id>/<id>/` is Harbor's job folder: each trial's `result.json`, logs and trajectory.
   `swarmory job show ID` lists each trial's reward and error without a download.

## What you can check locally

These need no Harbor, Docker or Modal:

- The task's own tests, such as `pytest tests -q -p no:cacheprovider` in the task folder, with the Python the mission
  set up (`harbor-python` in the eval-factory mission).
- The lint: `python3 <eval-factory skill>/scripts/lint_task.py PATH`. Its `sealed` row says whether the server
  will accept the task's network settings. A model-judge verifier passes the lint, but the server refuses it.
- The task's own scripts, such as `tests/verifier.py` on a stored case or `solution/solve.sh` against a copy of the
  starting files.

Then let the oracle and nop jobs prove the task through Harbor.

## Folder jobs

PATH is one task folder, or a folder whose subfolders are tasks. A folder job runs every task in one job, and its
trials run at the same time: attempts times tasks, at most 8 at once. `--attempts` takes any whole number from 1.
Run one job per variant folder, and rerun only the variant that changed.

## Custom harnesses

`--agent` also takes a committed folder of your own: `swarmory job harbor PATH --agent agents/env-mcp --model M`.
It must be inside the repo (not the repo root), have no uncommitted changes, and hold an executable `run`.
A run on Claude subscription logins can't serve a harness's Claude calls: Anthropic answers a subscription login
only from Claude Code itself. Use a run on API keys, or a model of another provider, for a custom harness.

| In the trial | What it is |
|---|---|
| `/installed-agent/harness/` | the folder, readable by the task's agent user; `run` runs as that user, in the task's working directory |
| `$HARBOR_INSTRUCTION` | the task's instruction |
| `$MODEL` | `--model` |
| `$ANTHROPIC_BASE_URL`, `$ANTHROPIC_API_KEY` | the run's model gateway, Messages API (`POST $ANTHROPIC_BASE_URL/v1/messages`) |
| `$OPENAI_BASE_URL`, `$OPENAI_API_KEY` | the same gateway, OpenAI's APIs; both bill the job and stop at `--budget` |
| `$MCP_CONFIG` | a JSON file: `{"mcpServers": {NAME: {"transport", "command", "args" or "url"}}}` from the task's MCP servers |
| `/logs/agent/run.log` | what `run` printed; it is in the job's artifacts |

A non-zero exit from `run` fails the trial. The trial has no network but the gateway, so `run` installs nothing:
its runtime and libraries come from the task's `environment/Dockerfile` or are in the folder.

An MCP server that lets the agent work the environment belongs to the task. Bake it into the main container in
`environment/Dockerfile` and declare it in task.toml. Use `transport = "stdio"` so the harness starts it as a child
process, or an HTTP server the image starts on 127.0.0.1. A compose sidecar is refused.

```toml
[[environment.mcp_servers]]
name = "env"
transport = "stdio"
command = "python3"
args = ["/opt/env-mcp/server.py"]   # COPYed in by environment/Dockerfile
```

A `run` is usually a model loop: start each stdio server in `$MCP_CONFIG`, give the model its `tools/list` as tools,
run each `tool_use` with `tools/call`, and stop when the model answers without one. Its model call, with Python's
standard library alone:

```python
import json, os, urllib.request

def model(messages, tools):
    body = {"model": os.environ["MODEL"], "max_tokens": 1024, "messages": messages, "tools": tools}
    req = urllib.request.Request(os.environ["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1/messages",
                                 json.dumps(body).encode(), {"content-type": "application/json",
                                                             "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                                                             "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)
```

Commit `run` executable (`chmod +x run` before `git add`).

## Commands

```
swarmory job harbor PATH --agent A [--model M] [--attempts N] [--env docker|modal]
                         [--budget USD] [--hours H] [--to WHERE]
swarmory job list [--mine]     the run's jobs, newest first
swarmory job show ID           each trial's reward and error
swarmory job cancel ID         only a job you queued
swarmory results PATH          every job on this folder's current content
swarmory get FILE [DIR]        download a job's attached output
```

## Read the result

- Read the errors before the rewards. An errored trial is not a wrong answer: it counts under "errored", not
  "failed". `swarmory job show ID` lists each trial's reward and error.
- `EnvironmentStartTimeoutError` that survived Harbor's retry, or "Harbor exited N with no trial results", is
  infrastructure. Rerun that task once, alone. If it fails again, say so in the issue.
- `ImageBuildError` is a bug in the task's Dockerfile. Fix the task. A rerun fails the same way.
- With no network, Harbor can't install anything at setup or at grading time. Install every tool the agent or the
  verifier needs in the task's Dockerfiles. A Reward Kit verifier needs `harbor-rewardkit` installed there; its
  `uvx` line in test.sh can't download it during grading.

## Share the run

- The server refuses to cancel another member's job. Ask its owner.
- Cancel your own jobs on a commit you replaced. A canceled job's trials can start no new model call.
- After you queue, go on with other work. The result is a message that @mentions you, so your next turn shows it.
  Don't poll `job list` or sleep in a loop.
- Don't ask the operator to run Harbor for you. Queue a job.
