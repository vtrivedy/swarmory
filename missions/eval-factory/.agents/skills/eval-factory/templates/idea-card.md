# <Task name>

Answer each section in a few plain sentences, or write "unknown".

## Claim

Write "capability" if the task targets an intermediate pass rate to measure progress.
Write "regression" if the task guards against backsliding at a near-complete pass rate.
Write one sentence that names the target model, the agent CLI, the capability, and the hard condition.

## Untested

List what the claim leaves out so readers do not overstate what the score measures.

## Evidence

Describe what a solver with the capability leaves in files or service records.
Describe what a solver without the capability leaves instead.
Name the exact difference that a script can read.

## Why it matters

Cite a real run, a reported failure, or a hunch, and state which one it is.

## Closest existing task

Name the closest public task, what this task adds, and the searches you ran.

## Cheapest shortcut

Name the cheapest way to pass without the capability, and explain why it fails here.

## Control and abstain variants

Name a control variant where a blanket policy fails.
Name an abstain variant where taking no action is right, paired with an answerable case.

## Harder condition

Name one condition you can tighten on a copy to test stronger judgment without adding scale.

## Keep rule

Write the target range of passes out of 10 runs before you start Phase 5.

## Versions

Add one bullet per version, newest last.
Use `v<N>` in every reviewer brief.

- **v1, commit `<sha>`.** Record changes, free check results, reviewer verdicts, model results, and failure causes. Log every defect found, which versions it affects, and whether scores stay comparable to prior versions.
