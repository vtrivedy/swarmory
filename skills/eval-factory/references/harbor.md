# Harbor commands

Checked on Harbor 0.23 with Modal. On another version, run `harbor --version` and check flags with `--help`.
Contents: [free checks](#check-the-task-with-no-model), [a shell in the image](#open-a-shell-in-the-solvers-image),
[model runs](#run-models), [reruns](#rerun-infrastructure-failures), [regrades](#regrade-without-model-calls),
[counting](#count-the-results), [all 0 or all 1](#all-0-or-all-1), [the report](#write-the-report), and
[cleanup](#clean-up-after-an-interrupted-run).

`S` is this skill's `scripts/` folder. `TASK` is the absolute path to a task folder. `tasks/` holds task folders, and
`<task-name>` is a bare folder name. `ENDPOINT` is the model endpoint's base URL, and `ENDPOINT_API_KEY` holds its key.
`python` is the Python that has Harbor and Modal installed. In a uv project, that is `uv run --no-sync python`.

## Check the task with no model

Commit the task on a work branch before every check and run. Harbor uploads the folder on disk, so an uncommitted file
can pass a check that a clean checkout fails. The commit also records what produced each result.

```bash
python $S/verify_task.py doctor $TASK                     # changes nothing: Harbor version, task.toml, Modal auth, clean git
cd $TASK && uvx pytest tests/ -q -p no:cacheprovider      # stored cases, in seconds
python $S/lint_task.py $TASK                              # isolation settings, reward clearing, the reference case
python $S/verify_task.py free $TASK \
	--endpoint https://<endpoint host>/anthropic \
	--harness claude-code=anthropic/<model> --harness codex=openai/<model> \
	--smoke-model <small model> [--out <new evidence folder>]
```

`free` runs the git, loader, stored-case, and lint checks. Then it runs five kinds of Harbor job in parallel: two
preflights (no network, and endpoint allowed), the oracle, the empty agent, and one install per harness. It strips
provider keys, so no job reaches a model. Exit 0 means no check failed. It takes 20 to 90 seconds. `--harness` takes
Harbor's `provider/model` form, because the install check resolves it through Harbor.

`free` writes a smoke-run command at the end of `summary.md`. One smoke run from a small model shows that the harness
starts, reaches the endpoint, and reads the instruction as written. Its reward says nothing about difficulty.

Read `summary.md` in the evidence folder first. Each row names its job and the file that proves it. Apply these rules:

- Judge a trial by its `result.json`. Harbor exits 0 when a trial errors.
- Take the reward from the verifier in its own container. A local grade skips the verifier's image, user, and
  artifact copy.
- Give every zero a reason: a `failed_check`, a blocked preflight line, or Harbor's exception.
- A `skip` row is unverified, and the verify gate needs none.
- A 402 or 429 from the endpoint is an infrastructure failure. Fix the endpoint before you rerun.

| Symptom | Cause and fix |
|---|---|
| A clean checkout fails the reference case and the working tree passes | A `.gitignore` rule hid a required file. `doctor` fails on ignored files. |
| `ValueError: Either datasets or tasks must be provided.` | Harbor couldn't load the folder. One cause is `environment_mode = "shared"` with a `[verifier.environment]` table. |
| `FileNotFoundError: ... tests has no environment definition` | Separate mode needs `tests/Dockerfile`. |
| `VerifierOutputParseError: Non-numeric reward` | `reward.json` holds numbers only. Put the record in `grade.json`. |
| `ConflictError: sandbox was created without a domain allowlist` | Use `--allow-environment-host`, not `--allow-agent-host`. |
| `Temporary failure resolving 'deb.debian.org'` | A no-network task can't install a harness at setup. Install it in `environment/Dockerfile`. |
| A harness behaves differently after a rebuild | `npm install -g` picked up a new release. Pin harness versions. |
| The oracle and empty-agent rows merge in a summary | Summarize the two jobs one at a time. |
| An optimization reference at parity fails the oracle | Pass `--oracle-threshold 0` to `free`. |
| The lint's last row fails on a working verifier | The lint runs `verifier.py <output dir> <verifier dir>` on `tests/cases/reference/output`. Keep that interface, and find other artifacts beside `output/` as [verifiers.md](verifiers.md#store-cases-for-several-artifacts) shows. |

The preflight trial also runs the verifier on an empty output. Ignore that reward.

## Open a shell in the solver's image

Build the image once. Then give the auditor and the hacker the run command as `<shell command>`. Each call runs one
command as the solver, with no network, and keeps `/app/output` in a folder on the host. It needs Docker. For a task
with `environment/docker-compose.yaml`, use `docker compose -f <that file> run --rm` with the same options.

```bash
docker build -t <task-name> tasks/<task-name>/environment
docker run --rm --network none -v "$PWD/<output location>":/app/output <task-name> bash -c '<command>'
```

## Run models

Take the model name from the endpoint's list, never from memory. Pass it to `-m` exactly as listed:

```bash
curl -s "$ENDPOINT/v1/models" -H "x-api-key: $ENDPOINT_API_KEY" -H "anthropic-version: 2023-06-01"   # Anthropic
curl -s "$ENDPOINT/v1/models" -H "Authorization: Bearer $ENDPOINT_API_KEY"                          # OpenAI
```

Every model uses the same command. Only `-m`, `-k`, and the job name change:

```bash
harbor run -p tasks/ -i <task-name> -a claude-code -m <model> -k 2 -n 2 --env modal \
	--allow-environment-host <endpoint host> --job-name <purpose>-<task-name>-<commit> \
	--ae 'ANTHROPIC_BASE_URL=<endpoint>' --ae 'ANTHROPIC_API_KEY=${ENDPOINT_API_KEY}'
```

- `-k` is runs per task, and `-n` is how many run at once. `<purpose>` is `iterate`, `heldout`, or `measure`.
- Iteration uses 1 or 2 runs. Measurement uses at least 10 per task on a frozen version.
- A trial's folder is `jobs/<job>/<trial>/`. It holds `result.json`, `verifier/grade.json`, and the transcript under
  `agent/`.
- `--allow-environment-host` gives the solver the model endpoint and no other network.
- With `ANTHROPIC_BASE_URL` set, Harbor sends `-m` exactly as written. A name the endpoint doesn't list fails every
  trial.
- Other harnesses are `-a codex` and `-a antigravity-sdk`. For your own agent, wrap it as a Harbor agent class and
  pass `-a <module>:<AgentClass>`.
- Pass endpoint settings with `--ae`, never in a committed file. Single quotes let Harbor substitute the key.
- Codex through an endpoint other than OpenAI's needs that endpoint declared under `[model_providers.<name>]` in
  `~/.codex/config.toml` inside the image. Otherwise the endpoint rejects Codex's compaction requests mid-run.
- Pin the provider when several serve the model. They differ in quantization and context limits.
- When another system runs Harbor jobs for you, check whether it gives the verifier network and fills `${...}` values.
  A verifier that makes decision calls needs both.

## Rerun infrastructure failures

Resume a job with the provider errors filtered out. The resume reruns those trials, model included:

```bash
harbor job resume -p jobs/<job> -f ApiResponseStalledError -f ApiRateLimitError -f ApiOverloadedError \
	-f OutputTokenExceededError -f NonZeroAgentExitCodeError
```

A harness that exits 0 after a stall leaves no error type. Delete that trial's folder before you resume.

To retry during a run, add `-r <retries> --retry-include <type>` once per type. Always name the types. Without
`--retry-include`, Harbor also retries `ContextWindowExceededError`, which is a scored fail.

## Regrade without model calls

Regrading reruns the verifier on every stored run and writes a new job. Use it after a verifier change or to test a
second reading of the instruction. The task needs separate mode, declared `artifacts`, and the same task name.

```bash
harbor job regrade jobs/<job> -p tasks/<task-name> --env modal --job-name regrade-<task-name>-<verifier-version>
```

## Count the results

```bash
python $S/summarize_runs.py jobs/<job> [jobs/<job> ...] --first 3 [--json]
python $S/probe_odds.py wilson <passes> <runs>
python $S/probe_odds.py set summary.json
python $S/probe_odds.py paired BEFORE.json AFTER.json
python $S/probe_odds.py rule --attempts <runs> --keep <low> <high>
```

- `--first N` keeps the first N clean runs per task and model, in start order, so reruns can't pick the best result.
- `--infrastructure-failure <trial-name>` marks a run as an infrastructure failure when only its trace shows the break.
- `wilson` gives one task's interval. `set` gives each model's mean pass rate over tasks, clustered by task, with
  pass@k and pass^k. It needs a `--first N` summary with at least two tasks. `paired` gives the interval of the
  per-task difference between two `--json` files, and needs at least two tasks.
- `rule` shows how often a keep rule (`--keep`) or kill rule (`--kill-at`) fires at each true pass rate. Run it before
  you measure, to choose the rule.

## All 0 or all 1

If every run scores 1, read the shortest passes against the [known exploits](hardening.md#known-exploits). If the
passes are valid, [raise difficulty](../SKILL.md#raise-difficulty).

If every run scores 0, stop at the first step that finds a cause:

1. Run `summarize_runs.py`. A run with no grade is an infrastructure failure, not a 0.
2. Run the oracle. If the reference doesn't score 1, the key, the verifier, or `solve.sh` is wrong.
3. Tally `failed_check` across runs. One check that fails in every run points to the verifier or an unstated rule.
4. Send the reader and the auditor. A different valid answer means the key or the instruction is wrong.
5. If every failure is the capability, build an easier version that some model passes.

A task not built from this skill's template has no `preflight/`, `tests/verifier.py`, or `tests/cases/`. Skip
`doctor`, the lint, `free`, and step 3, and run the oracle with `harbor run ... -a oracle`.

## Write the report

| Item | Where it comes from |
|---|---|
| Setup | Model, harness and version, provider, reasoning effort, and time limit. A score belongs to all of them. |
| Pass rate with its interval | `wilson` for one task, `set` for several. `set` clusters by task, because runs of one task are correlated. |
| pass@k and pass^k | `set`. pass@k counts a task if any of k runs passed. pass^k counts it only if all k passed, which is what an unattended agent needs. |
| Partial score | `partial_score` in each `verifier/grade.json`. It shows near misses that the all-pass score hides. |
| Cost | The harness's token and time usage in each trial's `agent/` folder. Report it with the early-stop count, because a run that quits early is cheap. |
| Early stops and false success claims | Read them from the transcripts, and report each as a count of runs. No script computes them. |
| Empty answer and untested scope | The `nop` row from `free`, and the card's Untested field. |
| Paired difference | `paired`, with at least two tasks. Two overlapping intervals don't show whether a change helped. |

> **Refund task.** The figures are invented. Setup: the production agent at harness version 2.4, `<model>` at high
> reasoning effort from one pinned provider, a 30-minute limit, and 10 runs per folder. Before a prompt change,
> `refund-committed` passed 6 of 10 runs (31% to 83%), and `refund-uncommitted` passed 10 of 10 (72% to 100%). `set`
> gives a mean of 80% (95% interval 41% to 100%), pass@10 of 2 of 2 tasks, and pass^10 of 1 of 2. The mean partial
> score was 0.91, and the mean cost was $0.40 per run. 2 of 20 runs stopped early, and 3 of 20 claimed success on a
> wrong result. After the change, `refund-committed` passed 9 of 10 (60% to 98%), and `refund-uncommitted` passed 10
> of 10. `paired` gives +15 points (-14 to +44), so the change is not shown to help. The empty answer scores 0.
> Untested: refunds that need a manager's approval.

## Clean up after an interrupted run

Harbor stops each sandbox when its trial ends. After an interrupted run, stop only what that run started:

```bash
python $S/verify_task.py cleanup <evidence folder> --dry-run
python $S/verify_task.py cleanup <evidence folder>
```

`"run_finished": false` in `cleanup.log` means the run was cut short. Run `free` into a new folder before you trust
the task.
