# Run spec: <set or task name>

An optional file that records what the run owner approved
([SKILL.md](../SKILL.md#spend-only-what-the-run-owner-approved)). In a swarmory run, the grant at launch is the spec.

## Question

"Measure" or "improve", and the claim the set answers.

## Models and harnesses

| Role | Model | Harness | Reasoning effort |
|---|---|---|---|
| Target model, or your own agent | | | |
| A model from another provider, to check results aren't specific to one family | | | |
| Cheap model for iteration runs | | | |

## Endpoint

The inference endpoint and the name of the environment variable that holds its API key. Never the API key itself.

## Limits

- Time limit per run:
- Output-token cap per harness:
- Concurrent runs:

## Runs

- Cheap-model runs per task version while iterating:
- Target-model runs per task version while iterating:
- Measurement runs per model on the frozen version:

## Target pass rate

The range you aim for. For a frontier eval, it is the target model's pass rate across the set. For an eval of your own
agent, it is the agent's pass rate per task.
