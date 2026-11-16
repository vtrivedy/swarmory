# Assume the solver attacks the verifier

Strong models find shortcuts in ordinary runs, not only when asked to. Build the defenses into the task files, where a
script can check them, then read passing runs for anything they missed.

## Build isolation into the task files

```toml
schema_version = "1.4"
artifacts = ["/app/output"]

[environment]
network_mode = "no-network"

[agent]
user = "agent"

[verifier]
environment_mode = "separate"

[verifier.environment]
network_mode = "no-network"
```

- **Run the verifier in separate mode.** Harbor's default runs the verifier in the solver's container as the same
  user, where the solver can pre-write the reward file or replace the programs the verifier calls. Separate mode runs
  the verifier in a fresh container after the solver's container stops.
- **Declare every output the verifier reads in `artifacts`.** Harbor copies each artifact path into the verifier's
  container at the same path.
- **Ship a `tests/Dockerfile`.** In separate mode Harbor builds the verifier's image from `tests/`, and the verifier
  runs as that image's default user. Don't reuse the solver's Dockerfile, whose `USER agent` would run the verifier as
  the solver.
- **Run the solver as a user that isn't root.** Set `user = "agent"` under `[agent]` in `task.toml`, and end the
  solver's Dockerfile with `USER agent`. The lint in [verifying.md](verifying.md) checks both.
- **Clear old rewards first.** `tests/test.sh` deletes `/logs/verifier/reward.*` before grading, and fails if the
  delete fails.
- **Give the reference no special path.** A verifier that skips checks when a marker file exists will meet a solver
  that creates the file.
- **Allow no network beyond the model endpoint and the hosts the task needs.** A verifier that calls a model gets its
  own narrow access and its own API key ([verifiers.md](verifiers.md#decision-calls-for-narrow-judgments)).

## Verify the solver's code from outside it

Separate mode keeps the solver's files away from the verifier. It doesn't stop submitted code from forging a result
once the verifier runs it.

1. Copy the submission to a root-only path, and verify the copy.
2. Run it as an unprivileged user, with a timeout, in its own process group.
3. Read results from a file only root can write, never from the program's own output.
4. Count a crash or timeout as a failed check, and name it in `grade.json`.

These steps stop code that exits with success at import time, a shutdown hook that prints a passing line, answer text
that gets evaluated as code inside the verifier, and submitted code that imports a readable reference.

## Run a preflight inside the built image

The template's `preflight/preflight_checks.sh` runs inside the built image as the solver's user
([verifying.md](verifying.md#run-the-checks-cheapest-first)) and prints `ok` or `blocked` per check:

- the solver isn't root
- the tools the task needs run
- the solver can't list `/tests` or read the reference
- outbound network is blocked, tested with a real fetch. Under an allowlist, Modal accepts a TCP connection to any host
  and then resets the TLS handshake, so a bare connect proves nothing.
- the solver can't write to any Unix socket. A sandbox daemon's socket can expose files the solver shouldn't see.
- for a task that ships a git repository, add a check that `git for-each-ref` shows only the start branch and
  `git fsck --lost-found` finds nothing

## Perturb inputs instead of hiding tests

When the solver must see the tests, verify on perturbed copies with a fixed seed. Change everything a shortcut could
depend on: sign, scale, order, size, empty inputs. Fail the build if a memorized answer still passes on the perturbed
copies.

When the solver can score itself, show a validation score, never the test score. A solver that can run the real scorer
will fit it.

## Look for leaks outside the task

- **The harness.** Some harnesses read hidden folders or fetch outside content into their own files. Check what the
  harness puts in the container, not only the task.
- **Public run logs.** Another model's graded run, if public, can be found and copied. Keep run logs private while a
  task is in use.
- **The prompt.** Run it with the environment or media removed. If a model still answers, the prompt gives the answer
  away.

## Change the environment, not the prompt

Solvers quote anti-cheating rules and break them anyway. These work instead:

- a script for anything a script can decide
- one narrow yes or no check per known exploit, instead of one broad "did it cheat?" judge
- giving the solver the same legitimate tool the verifier uses, so it has no reason to work around it

Offer the solver a way to report that the task is broken. A report counts as a failure and as a finding.

## Check passing runs against the exploit list

Add each new kind of flaw to this list, and its defense to the section above it belongs in ([principle
8](../SKILL.md#principles)). Read every passing run from the target model for these. Each one you find becomes an
`exploit-<name>` stored case, and the run counts as a fail.

- Read `/tests`, the reference or the key.
- Wrote a reward file or changed the verifier's environment.
- Printed a passing line from a hook or a stub.
- Enumerated every possible answer.
- Replayed public reference files instead of doing the work.
- Found the answer in git refs, the reflog, dangling objects, a package cache, or a newer installed version of the
  package.
- Reached the sandbox's daemon socket.
- Wrote the solution into the harness's own files.
- Submitted early with a near-perfect self-check, using a means the task forbids.
- Used a forbidden method that still produces a correct output. No verifier catches this. Only the transcript does.
