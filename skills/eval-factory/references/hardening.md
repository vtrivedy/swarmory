# Hardening

Put each defense in the task files, where a script can check it. Then read passing runs for what the defenses missed.

## Isolate the solver from the verifier

The template in [templates/task/](../templates/task) already does each of these. Keep them:

- **`artifacts` in `task.toml`.** List every path the verifier reads.
- **A solver that isn't root.** Set `[agent] user = "agent"`, and end the solver's Dockerfile with `USER agent`.
- **Separate mode.** Set `[verifier] environment_mode = "separate"`. By default, the verifier runs in the solver's
  container as the same user, where the solver can pre-write the reward file or replace programs the verifier calls.
- **`tests/Dockerfile`.** Separate mode builds the verifier's image from `tests/` and fails without this file. Don't
  copy the solver's Dockerfile, which ends in `USER agent` and would run the verifier as the solver.
- **Clear old rewards.** `tests/test.sh` deletes `/logs/verifier/reward.*` first and fails if the delete fails.
- **No special path for the reference.** If the verifier skips checks when a marker file exists, a solver creates that
  file.
- **No network.** Set `network_mode = "no-network"` for the environment and the verifier. Allow only the model endpoint
  and the hosts the task needs.

[lint_task.py](../scripts/lint_task.py) checks `task.toml`, both Dockerfiles, and the reward clearing in `test.sh`.

## Run submitted code from outside it

Separate mode keeps the solver's files from the verifier. Submitted code can still forge a result when the verifier
runs it. So the verifier:

1. Copies the submission to a path the solver never wrote, and runs the copy.
2. Runs it as an unprivileged user, with a timeout, in its own process group.
3. Computes the verdict in the root process from the program's outputs. Expected values never enter a process that
   runs submitted code, because that code can forge the process's exit code and report.
4. Counts a crash or timeout as a failed check named in `grade.json`.

## Run the preflight inside the built image

[preflight_checks.sh](../templates/task/preflight/preflight_checks.sh) runs as the solver's user and prints `ok` or
`blocked` for each check:

- The solver isn't root, and `python3` runs. Add a line for each tool your task needs.
- The solver can't list `/tests` or `/solution`.
- Outbound network is blocked, tested with a real fetch. On Harbor 0.23 with Modal, an allowlist accepts TCP to any
  host and then resets the TLS handshake. A bare connect proves nothing.
- The solver can't write to any Unix socket. A sandbox daemon's socket can expose hidden files.

In a task with a git repository, add checks that `git for-each-ref` shows only the start branch and
`git fsck --lost-found` finds nothing. Add a line for each hole you close that a stored case can't show.

## Perturb inputs when the solver sees the tests

Grade on copies perturbed with a fixed seed. Change the sign, scale, order, and size, and add empty inputs. Store a
memorized answer as an exploit case that must score 0 on the perturbed copies. When the solver can score itself, show a validation score, never
the test score.

## Look for leaks outside the task

| Leak | Check |
|---|---|
| The harness reads hidden folders or fetches outside content into its own files | Inspect what the harness writes into the container. |
| Another model's graded run is public | Keep run logs private while a task is in use. |
| The instruction gives the answer away | Run it with the environment or media removed. A model that still answers found the leak. |
| The task is in training data | Keep some instances private, build instances from a seed, and add a canary string. |

## Known exploits

Check every passing run by the target model against this table. Each find becomes an `exploit-<name>` stored case or a
preflight line, and the run counts as a fail. Add each new kind here.

| Exploit | Defense |
|---|---|
| Read `/tests`, the reference, or the key | Root-only `/tests`, separate mode, and a preflight line, since the output looks correct |
| Wrote a reward file or changed the verifier's environment | Separate mode, reward clearing in `test.sh` |
| Printed a passing line from a hook or stub | Compare outputs to hidden expected values in the root process. Never trust a pass line that the submission or its test runner prints. |
| Enumerated every possible answer | Make the answer space too large to guess, or limit submissions |
| Replayed public reference files | Perturbed inputs |
| Found the answer in git refs, the reflog, dangling objects, a package cache, or a newer installed package | Ship a clean repository, preflight `git fsck` |
| Reached the sandbox daemon's socket | Preflight socket check |
| Wrote the solution into the harness's own files | Check what the harness copies out |
| Computed a summary number by a shortcut, such as a ratio of raw peak heights | Check the values the summary comes from |
| Changed state that the verifier didn't check, such as deleting a charge after refunding it | Service-owned state, and a check that nothing else changed |
| Used a forbidden method that still produces a correct output | Read the transcript of every pass |

Use one narrow check per known exploit instead of one broad "did it cheat?" judge.
