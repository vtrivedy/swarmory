# <Task name>

The task's record, from idea to frozen version. Answer each field in a few plain sentences, or write "unknown".

## Claim

"Measure" or "improve". Then one sentence: the target model, doing what, under which conditions.

## Evidence

What a solver with the capability does or produces, and what one without it does instead. Name the difference a script
can read. The verifier checks that difference.

## Why it matters

A run where a model failed at this, a reported failure, or a hunch. Say which.

## Closest existing task

The nearest task in any public benchmark, what this one adds, and the searches you ran.

## Cheapest way to pass without the capability

And why it won't work.

## Versions

Add one entry per version, newest last. This is the task's record, and the next builder reads it first.

- **v1, commit `<sha>`.** What changed. Free checks: pass or what failed. Each model you ran, its passes out of runs,
  and where the failures went wrong. What you'd change next, and why.
