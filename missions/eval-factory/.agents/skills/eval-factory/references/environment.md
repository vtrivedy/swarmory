# Environment and hardening

Contents: [task layout](#lay-out-a-task-folder), [synthetic data](#build-hybrid-synthetic-data), [isolation](#isolate-the-solver-from-the-verifier), [submitted code](#isolate-submitted-code), [mock services](#run-a-mock-service-the-solver-cannot-edit), [preflight](#run-the-preflight-inside-the-built-image), [perturbed inputs](#perturb-inputs-when-the-solver-sees-the-tests), [leak searches](#search-for-leaks), and [known exploits](#known-exploits).

## Lay out a task folder

Copy the starter template from the repository root.

```bash
mkdir -p tasks work/<task-name>
cp -R <skill folder>/templates/task/. tasks/<task-name>/
```

Configure each file in `tasks/<task-name>/` as follows.

| File | Rule |
|---|---|
| `tests/<generator>.py` | Run on the host with a fixed seed. Write `tests/key.json`, its `how_established` field, and starting snapshots to `tests/`. Write solver data to `environment/data/`. Commit all outputs. |
| `environment/Dockerfile` | Add `COPY data/ /app/data/` before `USER agent`, and `chown` writable solver directories. Never write `COPY .`, because `lint_task.py` rejects it. Pin base tags while building and pin digests when freezing. |
| `tests/Dockerfile` | Add every file the verifier reads, including snapshots and hidden fixtures, to `COPY`. Keep `USER root`. |
| `task.toml` | Set `[agent] timeout_sec` to the instruction time limit plus 120 seconds for startup. Set `cpus` and `memory_mb` under `[environment]`. Fill `[metadata]`. List every artifact the verifier reads in `artifacts`. |
| `preflight/preflight_checks.sh` | Add one line per tool the claim does not test, plus one line per environment defense. |

Replace every starter placeholder before finishing Phase 2, because the starter files grade the answer 42 and pass checks on their own.

- Replace `[task] name` in `task.toml` with a valid `org/name` value, because Harbor rejects `<team>/<task-name>`.
- Replace `tests/key.json`, `solution/solve.sh`, and the checks in `tests/verifier.py`.
- Replace every case folder in `tests/cases/` and the rule assertions in `tests/test_cases.py`.
- Replace the task body and resource limits in `instruction.md` so they match `task.toml`.

For a no-network task, install and pin the agent CLI in `environment/Dockerfile` before `USER agent`.

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
	&& npm install -g @anthropic-ai/claude-code@<pinned-version> \
	&& rm -rf /var/lib/apt/lists/*
```

Store each paired variant in `tasks/<task-name>-<variant>/`, built by the same generator under a different condition.

## Build hybrid synthetic data

When a task needs realistic prose such as emails, tickets, logs, or reports, separate deterministic truth from natural-language generation. Unconstrained model generation invents contradictions, answer-coded names, and leaked solutions.

1. Build all IDs, dates, permissions, numeric facts, and entity relationships in seeded host code first.
2. Assign each fact an ID and pass only allowed fact IDs to the text generator. Never show the text generator the planned answer or rubric.
3. Compute `tests/key.json` strictly from the seeded structured state.
4. Validate generated files in code for schema shape, banned answer literals, and broken cross-references. Then inspect sample prose for faithfulness before freezing.

## Isolate the solver from the verifier

The starter template in [templates/task/](../templates/task) sets these controls. Keep all of them.

- **`artifacts` in `task.toml`.** List every path the verifier reads. Harbor copies only these paths into the separate verifier container.
- **Non-root solver.** Set `[agent] user = "agent"` in `task.toml` and end `environment/Dockerfile` with `USER agent`.
- **Separate verifier container.** Set `[verifier] environment_mode = "separate"`. Shared mode runs the verifier in the solver container, where the solver can pre-write rewards or replace binaries.
- **Verifier Dockerfile.** Separate mode builds the verifier image from `tests/Dockerfile` and fails if it is missing. Do not reuse `environment/Dockerfile`, which ends in `USER agent`.
- **Clear old rewards.** Make `tests/test.sh` delete `/logs/verifier/reward.*` and `/logs/verifier/grade.json` first and fail if deletion fails.
- **No reference marker shortcut.** Never let the verifier skip checks when a marker file exists, because a solver can create that marker.
- **No network by default.** Set `network_mode = "no-network"` under `[environment]` and `[verifier.environment]`. Pass model endpoints at runtime with `--allow-environment-host` on `harbor run`. The linter allows a `[verifier.environment]` allowlist only when `[metadata] verifier_kind` is `"decision call"` or `"rubric judge"`.
- **Clean state per trial.** Start every trial from a fresh container with no shared writable host mounts or mutable remote state. Residual files leak side effects across parallel or repeated trials.

Run [scripts/lint_task.py](../scripts/lint_task.py) to verify `task.toml`, both Dockerfiles, and reward clearing in `tests/test.sh`.

## Isolate submitted code

Separate mode keeps solver files away from the verifier image, but submitted code can still attack the grader at runtime. Defend against in-process and artifact exploits in six ways.

1. Copy only allowed files from the solver output into a clean starting copy that the solver never wrote.
2. Reject symlinks on every output and artifact path so solvers cannot point to precomputed files.
3. Never import untrusted solver code into the verifier process. In-process code can inspect Python stack frames, monkey-patch clocks or `__eq__`, or forge exit codes.
4. Run submitted code as an unprivileged user such as `nobody`, inside its own process group, with a strict timeout.
5. Run reference computations and solver submissions in separate processes so shared memory or GPU memory never leaks reference state.
6. Compare serialized non-error outputs in the root verifier process. Record crashes or timeouts as named failed checks in `grade.json`.

See [run hidden tests on a clean copy](verifiers.md#run-hidden-tests-on-a-clean-copy) for copyable Python code.

## Run a mock service the solver cannot edit

If the solver can write a mock service's state files, the solver can edit the ledger without calling the API. Run the service as `root`, keep its state in a root-only folder, and start it from a root-owned launcher.

Detect service errors or rate limits explicitly so outages fail as infrastructure errors instead of scoring 0.

In `environment/Dockerfile`, before `USER agent`, copy the service and launcher.

```dockerfile
COPY services/ /opt/services/
COPY start-services /usr/local/sbin/start-services
RUN mkdir -p /var/lib/payments && chmod 700 /var/lib/payments \
	&& chown -R root:root /opt/services /usr/local/sbin/start-services \
	&& chmod 755 /usr/local/sbin/start-services
```

Create `environment/start-services` to launch the service and keep the container running.

```bash
#!/bin/bash
python3 /opt/services/payments.py --state /var/lib/payments --port 8080 &
exec sleep infinity
```

Runtime behavior differs between Modal and Docker.

- **Modal.** Harbor replaces the image entrypoint with its own keepalive. Pass `--ek 'keepalive=["/usr/local/sbin/start-services"]'` on `verify_task.py free` and every `harbor run`.
- **Local Docker.** Harbor's Docker environment ignores `keepalive` and starts single-Dockerfile containers as the Dockerfile's final `USER`, which is `agent`. For local auditor and hacker shells, start the container manually with `docker run -d -u root ... /usr/local/sbin/start-services`. For full Harbor runs of root-owned single-container mock services, use `--env modal`.

If a task ever reuses a mutable external service across trials instead of replacing containers, test reset idempotently. Capture baseline state, mutate it, run reset, and assert exact baseline equality.

Add health and permission checks to `preflight/preflight_checks.sh`.

```bash
curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1 && ok "service_payments_up" \
	|| blocked "service_payments_up" "payments service did not answer"
test -w /var/lib/payments && blocked "service_state_private" "solver can write /var/lib/payments" \
	|| ok "service_state_private"
```

List the service ledger and call log in `artifacts` in `task.toml`. Store them beside `output/` in each case as [store cases for several artifacts](verifiers.md#store-cases-for-several-artifacts) shows.

## Run the preflight inside the built image

[preflight_checks.sh](../templates/task/preflight/preflight_checks.sh) runs as the solver user and prints `ok` or `blocked` per check. Keep its starter checks and extend them for your task.

- Confirm the solver is not root and required task tools run.
- Confirm the solver cannot list `/tests` or `/solution`.
- Confirm outbound network is blocked with an actual fetch. On Modal, an allowlist accepts TCP connections and resets TLS, so a bare socket connect proves nothing.
- Confirm the solver cannot write to any Unix socket, because sandbox daemon sockets can expose hidden files.
- For git repository tasks, check that `git for-each-ref` shows only the starting branch and `git fsck --lost-found` prints nothing.
- Add a preflight line for every environment hole you close that a stored case cannot demonstrate.

## Perturb inputs when the solver sees the tests

When the solver can inspect visible test cases, grade on copies perturbed with a fixed seed. Vary sign, scale, order, and size, and include empty inputs. Store a memorized answer as an `exploit-<name>` case that scores 0 on the perturbed copies. When a local check helper is required, expose only a validation score and never the test score.

## Search for leaks

Preflight checks only `/tests` and `/solution`, so hidden files copied elsewhere pass `free`. Run both commands against the built image. Both must print nothing. Add every file name from `tests/` to the first command.

```bash
docker run --rm --network none <task-name> bash -c \
	'find / -xdev \( -name key.json -o -name solve.sh -o -name verifier.py -o -name expected.json \) 2>/dev/null'
docker run --rm --network none <task-name> bash -c \
	'grep -rlF "<distinctive key value>" / --exclude-dir={proc,sys} 2>/dev/null'
```

Never copy `.env` files into images or pass secrets with `docker build --build-arg`. Build arguments persist in image layers. Never paste private task keys, hidden fixtures, or reference solutions into training-enabled public tools during development.

Check four leak paths outside the image as well.

| Leak path | Defense |
|---|---|
| Agent runtime reads hidden folders or fetches outside data | Inspect files the agent writes inside the container during trials |
| Public run logs expose solutions | Keep trial logs private while the task is active |
| Instruction reveals the answer | Run a model on `instruction.md` without the environment. A pass proves a leak. |
| Task enters training data | Keep private instances, generate from a seed, and add a leading canary comment to `instruction.md` |

Harbor strips a canary from `instruction.md` only when it appears as a leading comment line containing the word `canary`. Use HTML comment syntax `<!-- canary GUID <uuid> -->` or hash syntax `# canary GUID <uuid>` at the top of `instruction.md`.

## Known exploits

Exposing verifier source code primes reward hacking, and prompt-only anti-cheat rules fail under optimization pressure. Audit every passing trace from the target model against this table. Turn every discovered break into an `exploit-<name>` case or a preflight check, and count that run as a fail.

| Exploit | Defense |
|---|---|
| Reads `/tests`, `solve.sh`, or `key.json` | Separate verifier container, root-only `/tests`, and preflight check |
| Writes reward file or edits verifier environment | Separate mode and reward clearing in `tests/test.sh` |
| Inspects Python stack frames or imports verifier globals | Never import untrusted code in the verifier process |
| Monkey-patches clocks, sync calls, `__eq__`, or test hooks | Execute untrusted code out of process and compare serialized outputs in root |
| Symlinks or copies precomputed artifacts or checkpoints | Reject symlinks on every artifact and keep reference artifacts out of the solver image |
| Crafts matching error strings to pass equality checks | Verify valid non-error results before comparing outputs |
| Reads residual shared memory or GPU memory from reference runs | Run reference and submission in separate isolated processes |
| Forges a pass line from a hook or stub | Compare outputs to hidden expected values inside the root verifier process |
| Enumerates candidate answers | Make the answer space too large to guess, or reject extra lines and candidates |
| Replays memorized public test outputs | Perturb hidden test inputs with a fixed seed |
| Recovers answer from git refs, reflog, dangling objects, or caches | Ship a clean repository and run `git fsck` in preflight |
| Reaches a host or sandbox daemon Unix socket | Check writable Unix sockets in preflight |
| Stores solution inside agent runtime files | Inspect agent artifacts exported from trials |
| Uses a summary shortcut instead of real work | Verify intermediate outputs that only the real method produces |
| Mutates unchecked state such as deleting a duplicate charge | Keep service state root-owned and assert untouched state matches its snapshot |
| Uses a forbidden method that yields matching output | Read the full trace of every passing run |

Write one narrow check per exploit instead of one broad cheat-detection judge.
