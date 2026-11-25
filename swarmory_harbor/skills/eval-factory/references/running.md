# Run tasks in Harbor

These commands were checked on Harbor 0.23 on Modal. On another version, run `harbor --version` and check flags with
`--help`. Take model names, harness settings and the endpoint from the run spec, if there is one. A model run spends
only what the run owner approved ([SKILL.md](../SKILL.md#spend-only-what-the-run-owner-approved)). The examples use
Claude Code through an Anthropic endpoint. Swap `-a`, `-m` and the `--ae` settings for other harnesses.

In the commands below:

- `tasks/` is the folder that holds your task folders, and `<task-name>` is the bare folder name.
- `<skill>` is the path to this skill's folder.
- `<commit>` is the short hash of the task repo's commit. Keep task folders in git, and commit before every run so a
  result can be reproduced.
- `-n` is how many trials run at once. Add `-o <jobs folder>` to keep job output somewhere other than `./jobs`.

Before the first model run, prove the task folder works with the checks that call no model
([verifying.md](verifying.md)).

In a swarmory run you hold no Modal token or model key. Run Harbor as a job instead, which a job worker of the run
runs for you: `swarmory job harbor tasks/<task-name> --agent <oracle, nop, claude-code, codex or a harness folder>
[--model <model>] [--attempts <runs>] [--env <docker or modal>] --to <issue>`. A harness folder is a committed folder
of your own with an executable `run`, which a job runs inside each trial. Trials run in Docker on the server's machine when the
server is local and on Modal otherwise, unless `--env` says. Commit the task folder first. `swarmory job` refuses a
folder with uncommitted changes, and the job runs on the commit, so its result names exactly what ran.
`swarmory results tasks/<task-name>` lists every finished job on the folder's current content. The result comes as a message with the job's artifacts
attached, which `swarmory get` downloads: `job-<id>/<id>/` in the archive is Harbor's job folder, and
[Count the results](#count-the-results) reads it as it reads a job you ran. How jobs run and fail, and how to share a
run, is in the running-harbor-jobs skill (`skills/running-harbor-jobs/SKILL.md` in the mission).

A job runs only a sealed task. The server refuses a `task.toml` unless `[environment]` and any `[verifier.environment]`
have `network_mode = "no-network"` and nothing else about the network, `[agent]` and `[verifier]` set no network
field, and no `env` table holds a `${...}` value. It also refuses a `docker-compose.yaml` anywhere in the folder: a
job's task is one container that its Dockerfile builds. `lint_task.py` flags each of these. A model trial still
reaches the run's model gateway, which the job adds. So a task whose verifier calls a model
([verifiers.md](verifiers.md)) can't run as a swarmory job. The rest of this page is for running Harbor yourself,
outside a swarmory run. In a swarmory run, don't run `harbor`, `docker` or `modal` yourself.

## Run models

Use the same command for a cheap model and for the target model. Only `-m`, `-k` and the job name change.
`ENDPOINT_API_KEY` is the name of the environment variable that holds your endpoint's API key.

Take `-m` from the endpoint's own list of models, not from memory or an example. Anthropic-style and OpenAI-style
endpoints both list theirs:

```bash
curl -s "$ENDPOINT/v1/models" -H "x-api-key: $ENDPOINT_API_KEY" -H "anthropic-version: 2023-06-01"   # Anthropic
curl -s "$ENDPOINT/v1/models" -H "Authorization: Bearer $ENDPOINT_API_KEY"                          # OpenAI
```

Pass an `id` from that list exactly.

```bash
harbor run -p tasks/ -i <task-name> -a claude-code -m <model> -k 2 -n 2 --env modal \
	--allow-environment-host <model endpoint host> --job-name <cheap or target>-<task-name>-<commit> \
	--ae 'ANTHROPIC_BASE_URL=<endpoint>' --ae 'ANTHROPIC_API_KEY=${ENDPOINT_API_KEY}'
```

- `-k` is runs per task. A first look uses 1 or 2. A measurement uses more, on a frozen version.
- `--allow-environment-host` gives the solver's container the model endpoint and no other network.
- With `ANTHROPIC_BASE_URL` set, Harbor sends `-m` to the endpoint exactly as written, with no provider prefix
  removed. A name the endpoint doesn't list fails every trial before the model starts.
- For an eval of your own agent, run it through its Harbor adapter with `-a <module>:<AgentClass>`. In a swarmory
  run, use a harness folder instead (running-harbor-jobs skill): a job takes no `module:Class`.
- Other vendor harnesses are `-a codex` and `-a antigravity-sdk`. `harbor run --help` lists the rest.
- Pass the endpoint settings with `--ae`, never in a committed file. Put each API-key reference in single quotes so
  Harbor substitutes it.
- Keep concurrency moderate. Hundreds of runs started at once can trip an endpoint's rate limits.
- Codex through any endpoint other than OpenAI's needs that endpoint declared as its own provider in Codex's config.
  Otherwise Codex sends OpenAI-only compaction requests, and the endpoint rejects them mid-run.
- Before the first run on a new provider, send the instruction once to check it doesn't trip a content filter.

## Rerun infrastructure failures

Harbor 0.23 names the provider errors it recognizes in a failed harness's output, such as `ApiResponseStalledError`,
`ApiRateLimitError`, `ApiOverloadedError` and `OutputTokenExceededError`. Resume a job with those types filtered out:

```bash
harbor job resume -p jobs/<job> -f ApiResponseStalledError -f ApiRateLimitError -f ApiOverloadedError \
	-f OutputTokenExceededError -f NonZeroAgentExitCodeError
```

`-f` removes trials with exactly that error type, and the resume reruns them, model included. A harness that exits 0
after a stall leaves no error type, so remove that trial's folder by hand before resuming. A verifier error needs no
new model run. Regrade it instead.

To retry during the run instead, add `-r <retries> --retry-include <type>` to `harbor run`, once per type. Always name
the types. Without `--retry-include`, Harbor retries every error outside its default exclusions, and those don't
exclude `ContextWindowExceededError`, which is a scored fail.

## Regrade without model calls

Regrading reruns the verifier on every stored run of a job and writes a new job. Use it after a verifier error, after
a verifier change, or to test another reading of the instruction. The task needs separate mode, declared `artifacts`,
and the same task name as the stored runs.

```bash
harbor job regrade jobs/<job> -p tasks/<task-name> --env modal --job-name regrade-<task-name>-<verifier-version>
```

## Count the results

```bash
python3 <skill>/scripts/summarize_runs.py jobs/<job> [jobs/<job> ...] --first 3
```

Name any trial whose trace shows an infrastructure failure with `--infrastructure-failure <trial-name>`.
[measuring.md](measuring.md#count-every-run-as-a-pass-a-fail-or-an-infrastructure-failure) says how each run counts.
