# Measure only what the numbers support

Once a task fails the target model for the right reason, freeze it and measure it.
`python3 scripts/probe_odds.py wilson <passes> <runs>` computes the intervals below.

## Starting points

Use these when nobody has said otherwise, and say in the results that you did.

| Setting | Starting point |
|---|---|
| Cheap-model runs while iterating | 1 or 2 per task version |
| Target-model runs while iterating | 1 or 2 per task version that might have changed difficulty |
| Measurement | A frozen task version, with enough fresh runs that the interval answers the question. `probe_odds.py wilson` shows the interval before you run. |
| Harness | Each model's own harness, or for an eval of your own agent, that agent's harness |
| Time limit | Stated in the instruction. One hour is a common default. |
| Target for a frontier eval | The set's tasks spread across the band ([frontier-notes.md](frontier-notes.md#rubrics-verifiers-and-audits)), so it orders today's models and still orders next year's, and some model passes an easier version of each hard task |
| Target for choosing between models | The candidates' intervals don't all overlap |
| Target for an eval of your own agent | The agent passes each task some of the time, so improvement can show |

## A few runs are a feel, not a rate

| Result | 95% interval |
|---|---|
| 0 of 2 | 0% to 66% |
| 1 of 6 | 3% to 56% |
| 2 of 8 | 7% to 59% |
| 8 of 32 | 13% to 42% |

Use iteration runs to learn why the task passes or fails, not to estimate a rate. One pass in two runs is consistent
with a task the model nearly always solves.

## Something must be able to solve it

A task no model passes is either the best item in the set or a broken one. A wrong key stumps every model, so it
looks exactly like a hard task.

- Have someone solve a zero-pass task blind, with no clock and no access to the verifier, before trusting it. The
  [auditor brief](../templates/auditor-brief.md) sets this up.
- Keep an easier version measured beneath every hard task. A zero with a nonzero version beneath it is a measurement.
  A zero everywhere measures nothing.

## Freeze before you measure

Runs used to tune a task can't also measure it, because tuning picks the version that happened to land where you
wanted. Commit the task, pin its images, stop editing, then run fresh. Decide what counts as keeping a task before the
runs come back. `probe_odds.py rule` shows how often a rule keeps a task at a given true rate. After a change to the
verifier alone, regrade stored runs instead of running new ones.

## Count every run as a pass, a fail or an infrastructure failure

`scripts/summarize_runs.py` applies these rules to Harbor jobs. Its output calls an infrastructure failure
`infrastructure_failure`.

| What happened | Status |
|---|---|
| Verified, no harness error | pass or fail |
| Ran out the task's clock, or the model's context window | fail |
| The model refused (`AgentSafetyRefusalError`) | refusal. It stays in the denominator. |
| No verifier result, including a verifier error | infrastructure failure |
| Harness or provider error, such as a crash, a rejected request or a stream error | infrastructure failure |
| Ran out of output tokens | infrastructure failure. Raise the harness's cap. |

An infrastructure failure leaves the pass rate. Rerun it ([running.md](running.md#rerun-infrastructure-failures)), and
show coverage beside every rate. Harbor names most infrastructure failures itself. A harness that exits 0 after a
stalled API doesn't, so name those runs with `--infrastructure-failure`. Keep the first N clean runs per task and model,
in start order (`--first N`), so reruns can't pick the best result.

## Report the setup, the interval and the reason

- The setup with every score: harness, reasoning effort, time limit, tools and endpoint. The same model can score very
  differently with a different tool set or prompt.
- The interval with every rate. Overlapping intervals don't prove two rates are equal. To compare two models or two
  versions of an agent, use the interval of the difference, paired by task when both ran the same tasks
  ([Miller 2024](https://arxiv.org/abs/2411.00640)). `probe_odds.py paired BEFORE.json AFTER.json` computes it from two
  `summarize_runs.py --json` outputs.
- Set-level intervals from a bootstrap over tasks, resampling whole tasks with their runs. A 20-task set is about 20
  observations, however many runs it took.
- The order you expect between models, written down before the run. Picking the best and worst afterwards inflates
  differences that aren't real.
- Refusals in their own column.
- Why models differ, from the runs.

## Add these checks for a frontier set

- **A human baseline,** when the claim compares models to people.
- **Keys checked by a second expert,** for questions only experts can answer.
- **An outside check on the ranking.** A set should order models roughly as other trusted evidence does, or you should
  be able to say why it doesn't.
- **Model and harness apart.** Run one model in two harnesses before crediting the model.
- **Versions and retirement.** Version the whole task folder, drop results from other versions, and retire a task once
  the target models solve it instead of patching it in place.
