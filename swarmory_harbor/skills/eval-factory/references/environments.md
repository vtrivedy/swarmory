# Plan the environment before you build it

The environment is everything the solver acts on: data, tools, services, dynamics, opponents and the harness. It is
good enough when it reliably brings out the behavior the claim names. Isolation is in [integrity.md](integrity.md).

## Answer these first

| Question | Why it matters |
|---|---|
| What does the solver see at the start, and through which files or tools? | Everything the verifier needs must be reachable from here. |
| Which actions can it take, and which can't be undone or cost something? | These are where judgment shows. |
| What changes without the solver: a clock, a user, an opponent, scheduled events? | State that changes during the run tests whether the solver tracks it. |
| Where does the right answer come from? | Building it into the environment is safest. |
| How does the verifier read the outcome: a file, final state, a log, games? | It decides what the environment must record. |
| What's pinned: seeds, image versions, opponents, the harness? | Unpinned parts make runs disagree for no reason. |

## Choose how each dependency behaves

| Mode | Use when |
|---|---|
| Frozen | Content must stay stable, such as a document set or a repository at a pinned commit. |
| Simulated | Writes, failures, permissions or time must reset for each run. |
| Live | A safe, read-only dependency is hard to reproduce. Record what it returned, and say what differs from production. |

Default to frozen or simulated. An eval never writes to anything real.

## Data

- **Build the truth into the data.** Decide the causes or the answer first, generate the data from them, then add the
  realistic mess: mismatched IDs between systems, duplicate rows, unit differences, missing values with a stated cause.
  Every target can then be recomputed from the files alone.
- **Use real files when realism is the point,** and derive the key with a script a reviewer can rerun.
- **Hold what the real system would hold, and nothing random.** Beside what the decision needs, include the ordinary
  records a real system has, so telling what matters is part of the work.
  Messier tasks are harder at equal length ([METR](https://arxiv.org/abs/2503.14499)).
- **Enforce domain rules in the environment, not the prompt.**
  [integrity.md](integrity.md#change-the-environment-not-the-prompt) says how.
- **Check any data you didn't make.** Public answer sets contain wrong keys, and some questions can be answered
  without the data they come with.
- **Plan for contamination.** Build the environment with a seeded script you can rerun with a new seed, and keep
  some tasks private.

## Tools

- **Every tool that changes state reports what changed.** A tool that silently accepts a bad edit hides the feedback a
  careful solver would use.
- **A self-check tool uses the verifier's own comparison code,** so the two can't disagree.
- **Tool schemas load in every harness the solvers use.** A cheap-model run through each harness checks this.
- **Decide whether the solver gets a shell** or only the task's tools.
- **Make information cost something** when the claim is about choosing what to look at, for example with a query
  budget.

## Services and simulated users

- **Run mock services inside the task.** The solver has no network, so run each service in the environment, as a
  process in the main container or as another service in `docker-compose.yaml`. Keep real tool names, schemas and
  errors, and replace only what's behind them.
- **Let the service keep the record.** When the path matters, have the service log every call it receives. The
  verifier reads that log, which the solver can't edit.
- **Script the failures you're testing.** A service that fails the first call and succeeds on the second turns "gives
  up too early" into a check.
- **Give a simulated user a fixed scenario** when the task is a conversation: what it knows, what it wants, what it
  will and won't reveal, and when it ends. Run the reference against the simulated user and read the conversations.
  Count a run where the user reveals the answer, contradicts its scenario or ends early as an infrastructure failure,
  and measure how often that happens before any score counts ([tau2-bench](https://arxiv.org/pdf/2506.07982)).

## Simulations and clocks

- **Simulate history instead of patching state.** An environment built by running events forward stays consistent.
  A final state patched by hand leaves contradictions a careful solver will find.
- **Choose the clock: live, slowed or paused.** The same game can score 9% live and 75% paused, so the clock is a knob.
  Report it with every score.
- **Give the verifier internal state the solver never sees,** such as the true positions in a game.

## Code and repositories

- **Pin the repository and toolchain,** and give each run a fresh copy.
- **Confirm the failure before the solver starts.** A bug task's failing test fails on the untouched repository.
- **Verify behavior, regressions and scope.** Run the focused tests, the related suite, and a check that nothing
  outside the allowed paths changed ([verifiers.md](verifiers.md#code-checks)). Disabling a failing test is the
  classic shortcut.

## Games and opponents

- **Score against a fixed panel** of opponents, pinned by version, over a fixed schedule of games. Keep any practice
  opponent off the panel.
- **Fill the middle of the panel.** An opponent every model loses to separates nothing. Opponents of middling strength
  separate the field.
- **Capture the player at the deadline** from outside the solver, and verify that copy.
- **Treat an unresolved game as a verifier error, not a loss** ([verifiers.md](verifiers.md#handle-errors-one-way)).
- **Make random play fail.** Score a random player and confirm it loses.

## The harness

- **Use each model's own harness by default,** or for an eval of your own agent, that agent's harness. Install it in
  the image, and pin its version once the task is stable.
- **State the time limit, and raise the output-token cap.** Runs that hit either count as
  [measuring.md](measuring.md#count-every-run-as-a-pass-a-fail-or-an-infrastructure-failure) says.
