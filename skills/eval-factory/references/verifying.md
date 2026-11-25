# Prove a task works before a model run

A task is ready for a model run only when it works the way a solver meets it. The image builds, the solver's user can't
see the verifier, the reference scores 1 through Harbor, the empty answer scores 0, and each harness installs.
[`scripts/verify_task.py`](../scripts/verify_task.py) runs these checks on Modal, cheapest first, and keeps every job
and log in one evidence folder. None of them calls a model.

## In a swarmory run

You hold no Modal token, so `verify_task.py free` and `harbor run` don't apply. Do this instead:

1. Run the task's own tests and the lint, as in steps 1 and 2 below. The lint's `swarmory_job_sealed` row must pass,
   or the server refuses the job.
2. Commit the task folder.
3. Run `swarmory job harbor <task folder> --agent oracle` and `swarmory job harbor <task folder> --agent nop`. The
   oracle must score 1 and the nop 0. Read each trial's error before its reward.

The traps below still apply to what those jobs report. The rest of this page is for checks outside a swarmory run.

## Set up

1. Use Harbor 0.23 with Modal credentials. Run every command with the Python that has Harbor installed, so `harbor`
   and `modal` come from one environment. In a uv project, that is `uv run --no-sync python ...`.
2. Commit the task folder. Harbor uploads the folder on disk, so a file that exists only locally can pass a check
   that a clean checkout fails. The evidence records the commit and any uncommitted files.
3. Set two variables:

   ```bash
   S=<this skill's folder>/scripts
   TASK=<absolute path to the task folder>
   ```

4. Run `python $S/verify_task.py doctor $TASK`. It changes nothing. Each JSON line is one check: Harbor is 0.23,
   `task.toml` parses, the scripts exist, Modal authenticates, Harbor loads the task, and the folder is committed with
   no untracked or ignored files. The `modal` line also counts running Harbor sandboxes. A nonzero count means another
   run is live or an earlier one leaked. Never stop sandboxes because of that count.

## Run the checks, cheapest first

1. **The task's own tests.** `cd $TASK && uvx pytest tests/ -q -p no:cacheprovider` takes seconds and no network.
2. **Lint.** `python $S/lint_task.py $TASK` checks isolation in `task.toml` and the Dockerfiles, checks that
   `tests/test.sh` clears old rewards, and runs the verifier on the stored reference case. It needs Python 3.11 or
   later.
3. **The free Modal checks.** One command repeats steps 1 and 2, then runs these Harbor jobs in parallel: the
   preflight inside the image with no network, the preflight with the model endpoint allowed, the oracle, the no-op
   agent, and `--install-only` for each harness.

   ```bash
   python $S/verify_task.py free $TASK \
   	--endpoint https://<endpoint host>/anthropic \
   	--harness claude-code=anthropic/<model> --harness codex=openai/<model> \
   	--out <new evidence folder>
   ```

   It takes 20 to 30 seconds with Modal's image cache warm, and about 90 seconds when an image is new. It removes
   provider keys from Harbor's environment, so no job can reach a model. Exit 0 means no check failed.
4. **One smoke run.** It calls a model, so [the spend rule](../SKILL.md#spend-only-what-the-run-owner-approved)
   applies. `free` writes the exact command at the end of `summary.md`, with the endpoint's API key read from
   `ROUTE_KEY`:

   ```bash
   harbor run -p $TASK -a claude-code -m claude-haiku-5-5 -k 1 -n 1 --env modal \
   	--allow-environment-host <endpoint host> \
   	--ae 'ANTHROPIC_BASE_URL=https://<endpoint host>/anthropic' \
   	--ae 'ANTHROPIC_API_KEY=${ROUTE_KEY}' --job-name smoke -o <evidence folder>/jobs
   ```

   One attempt from a small model checks that the harness starts and reaches the endpoint, the task's tools run, and
   the model read the instruction as written. Its reward says nothing about difficulty, so never report it as a pass
   rate. A second attempt to see whether the first was unlucky is a probe, not a smoke run.

## Read the evidence

`free` writes everything into the `--out` folder, by default `verify-runs/<task>-<UTC time>`, and refuses a folder
that already exists. Read `summary.md` first. Each row names its job folder and the file that proves it. `summary.json`
holds the same rows with the commit and times, and has `"finished": null` until the run ends. Each check also leaves
`<check>.log` and `jobs/<check>/`.

Hold every proof to these rules:

- Judge a Harbor check from the trial's `result.json`, never from Harbor's exit code. Harbor exits 0 when a trial
  errors.
- Take the reward from the verifier running in its own container on what the solver's container produced. A local
  grade of a stored file doesn't prove the task works.
- Run the preflight inside the built image as the solver's user, under each network the solver will have.
- Give every zero its reason: a `failed_check` from `grade.json`, a blocked preflight line, or Harbor's exception.
- Count a `skip` row as unverified, not as a pass.
- Count an endpoint error, such as a 402 or 429 from a policy, as infrastructure. Stop and tell the run owner.

## Known traps on Harbor 0.23 and Modal

**Loading the task.**

- A `.gitignore` rule can hide required files. One kept `tests/cases/*/output/` out of git, so a clean checkout failed
  the reference case while the working tree passed. `doctor` fails on any ignored file in the task folder.
- Harbor treats a folder it can't load as an empty dataset and fails with
  `ValueError: Either datasets or tasks must be provided.`. One cause is `environment_mode = "shared"` together with a
  `[verifier.environment]` table.

**Lint and the preflight.**

- The lint's last check assumes the template's interface, `verifier.py <output dir> <verifier dir>`. Another
  interface fails that row with the verifier's error.
- Harbor passes the task folder only to its own oracle agent. The preflight script reaches the agent as
  `--ak script=...`, and the agent module must be on `PYTHONPATH`. `verify_task.py` does both.
- Under a domain allowlist, Modal accepts a TCP connection to any host and then resets the TLS handshake. Test the
  network with a real fetch.
- Use `--allow-environment-host`, not `--allow-agent-host`. On a sandbox created with no network, the agent flag fails
  with `ConflictError: sandbox was created without a domain allowlist`.
- `find -xdev` skips Modal's `/__modal` mounts, so the socket search must cross filesystems.
- The preflight trial also runs the verifier on an empty output. Ignore its reward.

**The oracle and nop jobs.**

- Separate mode builds the verifier's image from `tests/`. Without `tests/Dockerfile` the trial fails with
  `FileNotFoundError: Task environment directory .../tests has no environment definition`.
- Harbor rejects a `reward.json` with anything but numbers (`VerifierOutputParseError: Non-numeric reward`), and the
  trial counts as an infrastructure failure. Put the typed record in `grade.json`.
- Harbor has no `oracle_reward_threshold`. For an optimization task whose reference sits at parity, pass
  `--oracle-threshold 0` to `free`.
- Summarize the oracle and nop jobs one at a time. Together, two agents with no model can merge into one row.
- When `reward.json` has `partial_score`, Harbor's console table shows it as a second reward row.

**Harnesses.**

- With `network_mode = "no-network"`, Harbor can't install a harness at setup
  (`Temporary failure resolving 'deb.debian.org'`). Install each harness in `environment/Dockerfile`. Harbor skips
  its own install when the binary is already on `PATH`.
- Pin harness versions in the Dockerfile when you freeze the task. An unpinned `npm install -g` picks up a new release
  at the next image build.
- A passing install doesn't prove the harness reaches the model. The endpoint preflight checks the network, and only
  the smoke run checks the whole path.
- Codex needs any endpoint but OpenAI's declared as its own provider ([running.md](running.md#run-models)).

## Clean up after an interrupted run

Harbor stops each sandbox when its trial ends, including after an error, so a finished run needs no cleanup. After an
interrupted run, stop only what that run started:

```bash
python $S/verify_task.py cleanup <evidence folder> --dry-run
python $S/verify_task.py cleanup <evidence folder>
```

It stops the running sandboxes in Modal's `__harbor__` app whose `harbor.session_id` tag starts with a trial folder
name from that evidence folder. Trial names carry a random suffix, so the match can't reach another run. Cleanup never
deletes the evidence. In the line it appends to `cleanup.log`, `"run_finished": false` means the run was cut short, so
rerun `free` into a new folder before you trust the task.
