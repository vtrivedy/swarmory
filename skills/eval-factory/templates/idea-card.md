# <Task name>

Keep the task's record here, from the first idea to the frozen version. Answer each field in a few plain sentences,
or write "unknown". The refund task in [SKILL.md](../SKILL.md#1-brainstorm) shows a shortened example.

## Claim

Write "measure" if the score reports how well models do this. Write "improve" if the task finds where your own agent
fails, so you can change it. For "improve", the target is that agent in its own harness. Then write one sentence that names the target model and harness, the behavior, and the
condition that makes it hard.

## Untested

Name what the claim leaves out, so that nobody reads more into a score than it measures.

## Evidence

Describe what a solver with the capability leaves behind, and what a solver without it leaves instead. Name the
difference that a check can read.

## Why it matters

Cite a run where a model failed at this, a reported failure, or a hunch, and say which.

## Closest existing task

Name the nearest public task, what this task adds, and the searches you ran.

## Cheapest way to pass without the capability

Name it, and say why it fails here. If one blanket policy passes, such as "always retry", add a control variant where
that policy fails.

## Knob

Name the one condition you will change to make a harder version, and what it changes about what the solver must
figure out.

## Versions

Add one entry per version, newest last. `v<N>` is the version in every brief.

- **v1, commit `<sha>`.** What changed. Whether the free checks passed. Each model you ran, its passes out of runs, and
  the cause of each failure. Each reviewer's verdict. Each error found: what was wrong, how you found it, and which
  versions it affects. What you'd change next, and why.
