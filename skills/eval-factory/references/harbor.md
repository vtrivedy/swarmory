# Harbor commands and measurement

Commands, flags, reviewer dispatch, model runs, reruns, freezing, counting, and reporting for [SKILL.md](../SKILL.md). Verified on Harbor 0.18 with Docker and newer Harbor with Modal. Check your installed version with `harbor --version` and inspect flags with `--help`.

Contents: [prerequisites](#check-prerequisites), [reviewer dispatch](#dispatch-a-reviewer), [reference solution](#run-the-reference-solution), [free checks](#run-free-checks), [solver shell](#open-a-solver-shell), [model runs](#run-models), [reruns](#rerun-infrastructure-failures), [regrades](#regrade-without-model-calls), [freezing](#freeze-the-task), [counting and reporting](#count-and-report), [all-0 or all-1 triage](#triage-all-0-or-all-1), and [cleanup](#clean-up-after-an-interrupted-run).

Use these placeholders in the commands below.

- `S` is this skill's `scripts/` directory, and `TASK` is the absolute path to `tasks/<task-name>`.
- `ENDPOINT` is the Anthropic-compatible base URL, `OPENAI_ENDPOINT` is the OpenAI-compatible base URL, and `ENDPOINT_API_KEY` holds the API key.
- `python` is the Python interpreter with Harbor installed.

## Check prerequisites

Run these checks before Phase 1. Stop and report if a required check fails.

```bash
harbor --version            # Harbor 0.18 or newer
docker info >/dev/null      # required for --env docker and local shells
modal profile current       # required only when running --env modal
```

Then query the endpoint's model list as [run models](#run-models) shows. Confirm the list is not empty.

On Docker, Harbor enforces `no-network` and `allowlist` policies through an nftables sidecar. If your host Docker kernel lacks nftables support, run network-isolated Harbor jobs on Linux Docker or with `--env modal`.

## Dispatch a reviewer

Fill every placeholder in the brief template before starting the reviewer.

| Placeholder | Value |
|---|---|
| `<task name>`, `<claim>`, `<card path>`, `<version>` | Values from `work/<task-name>/idea-card.md`, with `<version>` formatted as `v2` |
| `<report path>` | `work/<task-name>/reviews/<role>-<version>.md` |
| `<skill folder>` | Absolute path to this skill folder |
| `<shell command>` | The `docker exec` command from [open a solver shell](#open-a-solver-shell) |
| `<output location>` | `/app/output` inside the reviewer container |
| `<cases folder>` | `work/<task-name>/reviews/hacker-<version>-cases/` |
| `<job folder>` | Harbor job directory path, or `none` during Phase 2 |

Start a fresh agent whose entire prompt is the filled brief. Use another model family when available, or start a fresh session with no shared context.

```bash
codex exec "$(cat <filled brief>)"
claude -p "$(cat <filled brief>)"
```

| Role | Required access |
|---|---|
| All reviewers | Read `tasks/<task-name>/` and write `work/<task-name>/reviews/` |
| Critic | Web search and `harbor datasets list` |
| Auditor and hacker | Docker for `<shell command>` |

## Run the reference solution

Run `solution/solve.sh` inside the built solver image. During Phase 2 environment building, compare `/app/output` against `tests/key.json` and skip the `cases/reference/` copy lines. During Phase 2 stored-case setup, copy outputs into `tests/cases/reference/`.

For a task with mock services, start the container with `docker run -d -u root --name <task-name>-ref --network none <task-name> /usr/local/sbin/start-services`.

```bash
docker build -t <task-name> tasks/<task-name>/environment
docker run -d --name <task-name>-ref --network none <task-name> sleep infinity
docker cp tasks/<task-name>/solution/solve.sh <task-name>-ref:/tmp/solve.sh
docker exec -u agent <task-name>-ref bash /tmp/solve.sh
mkdir -p tasks/<task-name>/tests/cases/reference/output
docker cp <task-name>-ref:/app/output/. tasks/<task-name>/tests/cases/reference/output/
docker cp <task-name>-ref:<artifact path> tasks/<task-name>/tests/cases/reference/
docker rm -f <task-name>-ref
```

## Run free checks

Commit `tasks/<task-name>/` on a work branch before every check and run. Harbor uploads files from disk, so a clean commit ties every result to a recorded SHA. `verify_task.py` checks git cleanliness strictly inside `tasks/<task-name>/`, so `work/<task-name>/` stays separate without breaking `doctor`.

```bash
python $S/verify_task.py doctor $TASK --env docker
cd $TASK && uvx pytest tests/ -q -p no:cacheprovider
python $S/lint_task.py $TASK [--container-verifier]
python $S/verify_task.py free $TASK --env docker \
	--endpoint "$ENDPOINT" \
	--agent claude-code=anthropic/<model> --agent codex=openai/<model> \
	--smoke-model <small model> [--container-verifier] \
	[--ek 'keepalive=["/usr/local/sbin/start-services"]'] [--out <evidence folder>]
```

- Pass `--env modal` when running on Modal. Pass `--ek` with the service launcher only on Modal.
- Pass `--container-verifier` when `tests/verifier.py` needs root privileges or container tools. `verify_task.py free --container-verifier` builds `tests/Dockerfile` and grades every stored case inside the verifier container automatically, including `hardening=False` on `exploit-*` cases.
- `free` strips model and judge API keys, then runs preflight, oracle, empty agent, and agent install checks. Preflight tests both blocked network and endpoint access. Exit 0 means zero failed or skipped rows.
- Run the smoke command printed at the end of `summary.md` once to verify agent startup and endpoint reachability. Ignore the smoke reward.
- Read `summary.md` first. Judge every trial by `result.json`, because Harbor exits 0 even when a trial errors. Ignore the verifier reward on the preflight trial.

To test stored cases in the verifier image during Phase 2, build it and loop over `tests/cases/*`.

```bash
docker build -t <task-name>-verifier tasks/<task-name>/tests
for c in tasks/<task-name>/tests/cases/*/; do
	d="$(mktemp -d)"
	docker run --rm --network none -v "$PWD/${c}output:/app/output:ro" -v "$d:/logs/verifier" \
		<task-name>-verifier bash /tests/test.sh
	echo "$(basename "$c"): $(cat "$d/grade.json")"
done
```

Mount any extra `artifacts` from `task.toml` read-only at their container paths in that loop.

| Symptom | Cause and fix |
|---|---|
| Clean checkout fails reference case while working tree passes | A `.gitignore` rule hid a required file. Fix `.gitignore` and commit. |
| `Task.is_valid_dir` fails on starter task | Replace `[task] name = "<team>/<task-name>"` with a valid `org/name` |
| `FileNotFoundError: ... tests has no environment definition` | Separate verifier mode requires `tests/Dockerfile` |
| `VerifierOutputParseError: Non-numeric reward` | Keep only numbers in `reward.json` and put details in `grade.json` |
| `ConflictError: sandbox was created without a domain allowlist` | Pass `--allow-environment-host` instead of `--allow-agent-host` |
| `Temporary failure resolving 'deb.debian.org'` | A no-network task cannot install the agent CLI at runtime. Preinstall it in `environment/Dockerfile`. |
| Optimization reference at parity fails oracle | Pass `--oracle-threshold 0` to `verify_task.py free` |
| HTTP 402 or 429 from endpoint | Endpoint billing or rate-limit outage. Fix the endpoint before rerunning. |

## Open a solver shell

Build the solver image once, then start one long-lived container per reviewer. Set `<role>` to `auditor` or `hacker`, and give the `docker exec` line as `<shell command>`. Tell the auditor to keep scratch files in `/tmp`, because extra files in `/app/output` fail verification.

```bash
docker build -t <task-name> tasks/<task-name>/environment
docker run -d --name <task-name>-<role> --network none <task-name> sleep infinity
docker exec -u agent <task-name>-<role> bash -c '<command>'
```

For a mock-service task, start the container as root with its launcher instead.

```bash
docker run -d -u root --name <task-name>-<role> --network none <task-name> /usr/local/sbin/start-services
```

After the reviewer finishes, copy outputs and service artifacts to `work/<task-name>/reviews/`, then remove the container.

```bash
mkdir -p work/<task-name>/reviews/<role>-<version>-answer/output
docker cp <task-name>-<role>:/app/output/. work/<task-name>/reviews/<role>-<version>-answer/output/
docker cp <task-name>-<role>:<artifact path> work/<task-name>/reviews/<role>-<version>-answer/
docker rm -f <task-name>-<role>
```

Score the auditor output locally when the verifier needs only host Python.

```bash
d="$(mktemp -d)"
python3 -I tasks/<task-name>/tests/verifier.py work/<task-name>/reviews/auditor-<version>-answer/output "$d"
echo "$d"
```

When the verifier needs root privileges or container tools, build the verifier image and score inside it. Mount any extra `artifacts` listed in `task.toml` read-only at their container paths too.

```bash
docker build -t <task-name>-verifier tasks/<task-name>/tests
d="$(mktemp -d)"
docker run --rm --network none \
	-v "$PWD/work/<task-name>/reviews/auditor-<version>-answer/output:/app/output:ro" \
	-v "$d:/logs/verifier" <task-name>-verifier bash /tests/test.sh
echo "$d"
```

Copy confirmed hacker breaks from `work/<task-name>/reviews/hacker-<version>-cases/` into `tasks/<task-name>/tests/cases/`.

## Run models

Fetch the exact model ID from the endpoint before running. Never type model names from memory.

```bash
curl -s "$ENDPOINT/v1/models" -H "x-api-key: $ENDPOINT_API_KEY" -H "anthropic-version: 2023-06-01"
curl -s "$ENDPOINT/v1/models" -H "Authorization: Bearer $ENDPOINT_API_KEY"
```

Keep flags identical across models on one agent CLI. Change only `-m`, `-k`, and `--job-name`.

```bash
harbor run -p tasks/ -i <task-name> -a claude-code -m <model> -k 2 -n 2 --env docker \
	--allow-environment-host <endpoint host> --job-name <purpose>-<task-name>-<commit> \
	--ae "ANTHROPIC_BASE_URL=$ENDPOINT" --ae 'ANTHROPIC_API_KEY=${ENDPOINT_API_KEY}'

harbor run -p tasks/ -i <task-name> -a codex -m <model> -k 2 -n 2 --env docker \
	--allow-environment-host <endpoint host> --job-name <purpose>-<task-name>-<commit> \
	--ae "OPENAI_BASE_URL=$OPENAI_ENDPOINT" --ae 'OPENAI_API_KEY=${ENDPOINT_API_KEY}'
```

- For Modal runs, replace `--env docker` with `--env modal`. When a Modal task runs root mock services, add `--ek 'keepalive=["/usr/local/sbin/start-services"]'`.
- Pass `--allow-verifier-host <judge host>` when a decision call or rubric judge needs runtime network access.
- Use `<purpose>` values `iterate`, `heldout`, or `measure`. Use `-k 1` or `-k 2` during Phase 3 and at least `-k 10` during Phase 5.
- Pass API keys with `--ae` using single quotes so Harbor substitutes environment variables at runtime instead of committing secrets.
- Pin the provider when multiple providers serve one model, because quantization and context limits vary by provider.
- Wrap custom agents as a Harbor agent class and pass `-a <module>:<AgentClass>`.
- For Codex on a non-OpenAI endpoint, write `~/.codex/config.toml` after `USER agent` so compaction routes to your endpoint.

```dockerfile
RUN mkdir -p ~/.codex && printf '%s\n' 'model_provider = "endpoint"' '[model_providers.endpoint]' \
	'name = "endpoint"' 'base_url = "<OPENAI_ENDPOINT value>"' 'env_key = "OPENAI_API_KEY"' > ~/.codex/config.toml
```

Model pass-rate ordering never proves task validity on its own. Inspect unusually fast passes and hard-task surprises first. Weak models passing often reveal leaks, and zero passes across strong models often reveal setup or verifier bugs.

## Rerun infrastructure failures

Resume a job while filtering out provider and runtime exceptions. Include both Harbor 0.18 and newer exception names so outages rerun cleanly.

```bash
harbor job resume -p jobs/<job> \
	-f ApiResponseStalledError -f ApiRateLimitError -f ApiOverloadedError \
	-f OutputLengthExceededError -f OutputTokenExceededError -f NonZeroAgentExitCodeError
```

If an agent exits 0 after stalling with no recorded exception, delete that trial folder before resuming. To retry automatically during a run, pass `-r <retries> --retry-include <type>` for each infrastructure error type. Never omit `--retry-include`, because `ContextLengthExceededError` and `ContextWindowExceededError` are scored fails.

## Regrade without model calls

On Harbor versions that provide `harbor job regrade`, rerun a modified verifier against stored trial artifacts without new model calls. Regrading requires separate verifier mode, declared `artifacts`, and the same task name.

```bash
harbor job regrade jobs/<job> -p tasks/<task-name> --env modal --job-name regrade-<task-name>-<verifier-version>
```

To test a shared second reading, copy `tasks/<task-name>` to a scratch folder with the same basename. Relax the copy's verifier to accept that reading, and regrade against the copy. If the runs pass, repair the ambiguous wording in Phase 4 and delete the scratch copy. On Harbor 0.18 without `harbor job regrade`, run `tests/verifier.py` directly on each trial's saved artifacts instead.

## Freeze the task

Complete these steps before Phase 5 measurement.

1. Resolve every `FROM` base image in `environment/Dockerfile` and `tests/Dockerfile` to a digest and pin `FROM <image>:<tag>@sha256:<digest>`.

```bash
docker buildx imagetools inspect <image>:<tag> | grep -m1 Digest
```

2. Confirm every package, agent CLI, and judge model uses an exact pinned version.
3. Rebuild images and run `verify_task.py free`. Commit `tasks/<task-name>/` and record the commit SHA in `work/<task-name>/idea-card.md`.

## Count and report

Summarize trials and compute intervals with [summarize_runs.py](../scripts/summarize_runs.py) and [probe_odds.py](../scripts/probe_odds.py). Scores always reflect the model and agent CLI pair, never the model alone. Changing preinstalled packages changes the evaluation condition, so never compare scores across package changes without rerunning under the fixed setup.

```bash
python $S/summarize_runs.py jobs/<job> [jobs/<job> ...] --first 10 --json > work/<task-name>/<model>.json
python $S/probe_odds.py wilson <passes> <runs>
python $S/probe_odds.py set work/<task-name>/<model>.json
python $S/probe_odds.py paired work/<task-name>/before.json work/<task-name>/after.json
python $S/probe_odds.py rule --attempts 10 --keep <low> <high>
```

- `--first N` keeps the first `N` clean runs per task and model in start order so reruns cannot cherry-pick passes.
- `--infrastructure-failure <trial-name>` marks silent outages visible only in traces.
- `wilson` computes one task's interval. `set` computes task-clustered mean pass rate, `pass@k`, and `pass^k` across two or more tasks. `paired` computes the interval of per-task differences between two single-model JSON summaries.
- `rule` evaluates keep and kill rules before measuring.

Write `work/<task-name>/report.md` with every item below. Base keep or release decisions on confidence intervals rather than bare point estimates.

| Report item | Source |
|---|---|
| Setup | Model ID, agent CLI and version, provider, reasoning effort, time limit, commit SHA, and `verifier_version` |
| Pass rate and interval | `probe_odds.py wilson` for one task, `probe_odds.py set` for multiple tasks |
| Reliability | `probe_odds.py set` reports `pass@k` for any pass in `k` trials and `pass^k` for all `k` passing |
| Partial score | Mean `partial_score` from `summarize_runs.py` and `verifier/grade.json` |
| Refusals and decoys taken | `summarize_runs.py` |
| Cost and tool calls conditioned on outcome | `mean_cost_pass` and `mean_cost_fail` from `summarize_runs.py`, plus token, runtime, and tool-call totals from `agent/` logs |
| Early stops and false success claims | Trace review counts of runs that stop before checking or claim success on wrong output |
| Expert baseline | Auditor blind-solve outcome and duration from `work/<task-name>/reviews/` |
| Baselines, limits, and comparability | `nop` score from `free`, untested scope, mocked limits, mitigations, flaw bounds, version comparability, and judge calibration rates |
| Paired difference | `probe_odds.py paired` when comparing two models or configurations across two or more tasks |

Retire or refresh any task that leaks into public training sets or reaches ceiling scores on target models. When retiring saturated tasks from a suite, keep a small fixed companion subset to track progress across versions.

## Triage all-0 or all-1

If every run scores 1, read the shortest and fastest passes against [known exploits](environment.md#known-exploits). If the passes are valid, [raise difficulty](../SKILL.md#raise-difficulty) on a copy.

If every run scores 0, stop at the first step that explains the failure.

1. Run `summarize_runs.py`. Trials with no grade are infrastructure failures, not zeros.
2. Run the oracle. If the reference solution scores 0, fix `solve.sh`, the key, or the verifier.
3. Tally `failed_check` across runs. One check failing everywhere points to a verifier bug or an unstated rule.
4. Send the reader and auditor. A different valid answer proves the instruction or key is wrong.
5. If every failure is a true capability failure, weaken one condition on a copy until at least one model passes.

For a legacy task without `preflight/`, `tests/verifier.py`, or `tests/cases/`, skip `doctor`, `lint_task.py`, `free`, and step 3. Run `harbor run ... -a oracle` instead.

## Clean up after an interrupted run

Harbor stops containers and sandboxes when trials finish normally. After an interrupted run, preview and stop leftover resources from that evidence folder. `verify_task.py cleanup` defaults to `--env docker`. Pass `--env modal` for Modal runs.

```bash
python $S/verify_task.py cleanup <evidence folder> --dry-run
python $S/verify_task.py cleanup <evidence folder>
python $S/verify_task.py cleanup <evidence folder> --env modal --dry-run
python $S/verify_task.py cleanup <evidence folder> --env modal
```

If `cleanup.log` records `"run_finished": false`, rerun `verify_task.py free` into a new evidence folder before trusting the task.
