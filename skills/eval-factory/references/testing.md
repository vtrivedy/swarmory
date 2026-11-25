# Test rough versions fast

Get from an idea to a task that measures what its claim says, in as few target-model runs as possible. The commands
are in [running.md](running.md).

## Choose the target model and the cheap model

Start from the decision the eval informs:

| The eval is for | Target model | What "working" looks like |
|---|---|---|
| Tracking the frontier | The strongest models available, each in its own harness | They fail most runs, for the reason the claim names |
| Choosing between models | The candidates, at the settings you'd deploy | The candidates score differently, for a reason you can name |
| Improving your own agent | Your agent, at its production settings | It passes some of the time, so a change can move the rate |

Pick a cheap model from the same provider and harness as the target, so tool-call formats and harness problems show
up in cheap runs. When the target is itself a cheap model, skip the separate cheap model and use more runs.

Each model you run has one job ([principle 7](../SKILL.md#principles)):

- **A cheap model finds bugs.** The tools run, the wording is clear, the environment behaves. If a cheap model passes,
  the task is too easy or has a shortcut. Read the run to find out which.
- **Two or three models of clearly different strength show where difficulty comes from.** Read the step the weaker
  model fails and the stronger one passes.
- **One target-class model stays out of every iteration run.** Measure on it too, so the task doesn't only fail the
  models it was tuned against.

Only the target model tells you whether the task is hard enough. A task tuned until a cheap model fails half the time
is often easy for a strong one, so never set difficulty from cheap runs.

## Iterate in small loops

1. **Rough version.** The smallest version that could show the behavior. Build it in hours.
2. **Cheap model, once or twice.** Read the runs. Did the harness start? Did each tool return what it promised? Which
   sentence did it misread, and would a careful person misread it too? Fix what you find and rerun. These loops are
   cheap, so take as many as you need.
3. **Target model, once or twice.** For a frontier eval, a quick pass with most of the time left means the task is too
   easy. Make it ask for more of the capability ([difficulty.md](difficulty.md)) or drop it. If it fails, read why
   before believing it.
4. **Fix and repeat.** Rerun the cheap model after big changes, and the target model when you need to know whether a
   change moved difficulty.
5. **Stop** when the task behaves the way the table above says it should and you or a reviewer can solve it
   ([measuring.md](measuring.md#something-must-be-able-to-solve-it)), or when you've learned the idea doesn't work.

## Read runs, not just scores

A score says a run failed. Only the transcript says why. For each run worth reading, ask whether a careful solver with
exactly the same information could have kept going. Find the first wrong step and quote it. Then name the cause:

| Cause | Meaning | What to do |
|---|---|---|
| Capability | It failed at what the task tests, with fair access to everything it needed. | Nothing. This is the measurement. |
| Missing or misleading information | Wording, a false fact, an unreadable input, or a fact it couldn't find. | Fix the task. |
| Tool or environment | A tool hid state, a service misbehaved, or the setup was wrong. | Fix the task. |
| Verifier rejected a valid result | The answer was right and scored 0. | Fix the verifier, then regrade stored runs. |
| Verifier accepted a wrong result | The answer was wrong and scored 1. | Fix the verifier, then regrade stored runs. |
| Exploit or leak | It passed without the capability, or saw what it shouldn't. | Close the hole in the environment, and keep the run as an `exploit-<name>` stored case. |
| Infrastructure | The harness, provider or sandbox broke the run. | Rerun it, unscored. |
| Luck | It passed on a guess. | Count it separately, and make guessing harder. |

Judge an exploit by what the run did, not by what its reasoning says. Fix a defect at its cause. Never make a task
harder to hide a defect in it.

Read every pass by the target model, since shortcuts and luck show up in passes. Read every run on a task no model
passes, and enough failures to say why each model fails. Across runs, ask:

- Did it use its time? Quitting early with a confident wrong answer is a different failure from running out of time.
- Did its final report match what it did? Count claims of "verified" on a wrong answer.
- Did it act as if partial credit existed? If several runs settle for "close enough", the instruction may not say that
  only a full solve passes.
- Did it use only the tools the task offers?
- Did it take the decoy?
- Did a pass skip work that every real solve needs? If the answer depends on logs from two hosts and the solver never
  opened one of them, the solver found a shortcut or saw the key. Read the passes with the shortest time or the fewest
  tool calls first.

## Treat a shared wrong answer as an alarm

When several models give the same wrong answer, the task is usually ambiguous before it is hard. Join the answers
across runs after every batch. If many runs misread the same sentence, fix the sentence. If many make the same wrong
inference at the hardest step, that may be the capability, and a blind solve by a reviewer decides.

## Regrade before you rerun

When most runs fail, write a verifier that accepts the other reasonable reading of the instruction, and regrade the
stored runs. It calls no model. If failures flip to passes, the task was ambiguous, and the fix is the instruction. If
they don't, the task is hard.

## Stop and rethink when

- you can't say why a run failed
- stronger models don't beat weaker ones across the set, by interval
- every run fails at the first step
- most of the budget is gone and no version fails for the reason the claim names
