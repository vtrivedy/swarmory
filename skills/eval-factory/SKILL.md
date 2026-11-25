---
name: eval-factory
description: Principles and patterns for designing frontier evals and environments as Harbor tasks, in any domain. Use when deciding what capability to measure or what agent behavior to improve, coming up with eval ideas, building a task's environment and verifier, raising its difficulty, closing shortcuts, running models on it and reading the runs, or deciding whether a task is worth keeping.
---

# Eval factory

This skill helps you design evals that tell you something true about a model or an agent. Each eval is a set of Harbor
tasks, and each task is an instruction, an environment and a verifier. The principles below are the rules. The order
of work is the usual one, not a required one.

In a swarmory run, the run runs Harbor for you. You never run `harbor`, `docker` or `modal` yourself, and you hold no
Modal token. Commit a finished task folder and run `swarmory job harbor PATH`, as the running-harbor-jobs skill says.
Where this skill or the create-task and rewardkit skills show a `harbor` or `docker` command, that skill takes
precedence.

Start by building a map of the skill. Read this page, then list every section of the references:

```bash
grep -n '^#' <skill folder>/references/*.md
```

The map below says which file answers which decision. When you reach that decision, read that file in full. In a test
run, builders who read only this page built easy tasks that all looked alike, and stopped with half their time left.

Each of these words has one meaning throughout the skill:

- **Capability.** The ability the task tests, such as "finds the root cause when a second explanation fits almost as
  well".
- **Target model.** The model or agent the eval is built to measure. It is often the strongest model available, but it
  can be any model, or your own agent in its own harness.
- **Cheap model.** A small, fast model that finds bugs in a task before you spend runs on stronger models.
- **Solver.** Whatever attempts the task: a model in a harness, or your agent.
- **Run.** One solver's attempt at one task, with the transcript Harbor records. Harbor calls it a trial.
- **Instance.** One concrete environment and key for a task. A task can ship several.
- **Key.** The truth: an answer, a property the answer must have, or a set of opponents.
- **Verifier.** The program that compares what the solver produced with the key and writes the reward.
- **Reference.** The builder's own solution. Harbor's `oracle` agent runs it.
- **Empty answer.** What a solver that does nothing leaves. Harbor's `nop` agent produces it.
- **Check.** One pass or fail row in the verifier's record.
- **Stored case.** An output kept beside the verifier with the reward it must get.
- **Harness.** The program that runs a model as an agent: its tools, its prompt and its loop.
- **Knob.** The one condition you change to make a harder version of a task.
- **Decoy.** A planted wrong explanation that fits the evidence almost as well as the true one.
- **Run owner.** The person who approves model spend, usually the person you work for.

## Start from the question

Every eval answers one of two questions:

- **What capability are we measuring?** The answer is a claim someone would act on, such as which model to deploy or
  which research direction is working.
- **What do we want our agent to get better at?** The answer is a behavior you have seen it get wrong, or expect it to.

Then name the target model and work backwards. Write the claim. Write what a solver with the capability would do or
produce, and what one without it would do instead. Design a situation that forces that difference into the open.
Design the verifier that tells the two apart. Write the instruction last.

## Principles

1. **The task teaches what it rewards.** Agents learn from evals and from tasks like them. Make passing require a
   behavior you'd want in a good agent, such as checking a plausible answer before acting on it, getting missing
   information, or recovering from a failed step.
2. **Hard to do, cheap to check.** The best tasks need the capability to solve and only a script, or a narrow decision
   call, to verify. If you can't find a cheap verifier, redesign the task until one exists, or drop it.
3. **Build the truth into the environment.** Where you can, decide the answer first and build the data, repo or game
   from it. The key is then right by construction, and the verifier doesn't depend on anything the solver says about
   its own work.
4. **Verify the outcome, and accept every valid one.** Check the final state, the output or the behavior, not the path
   taken, unless the path is the capability. Use code where code can decide, a decision call where a narrow yes or no
   needs reading language, and a rubric judge only where neither works.
5. **State everything you verify.** The instruction states every rule the verifier enforces, unless the environment
   shows the rule and finding it is the capability. Every fact in the instruction is true. Describe the answer, not
   the check that confirms it. If the instruction describes the check, the solver runs the check instead of using
   the capability.
6. **Raise difficulty by asking for more of the capability, not more of everything.** More data, more steps of the
   same kind, a longer instruction or a tighter clock make a task bigger, not more informative. A harder version needs
   more judgment, such as a closer decoy or a missing fact to go find.
7. **Separate models of different strength.** A good eval orders models by capability. While you develop a task, run two
   or three models of clearly different strength, such as a small, a mid-sized and a frontier model. Read where each one
   fails. A step the weaker model fails and the stronger one passes is a kind of difficulty that tracks capability. If
   the strongest model passes most runs, push that difficulty further or find another one it still fails for the right
   reason. If every model fails at the same step, suspect the task. Run the strongest model only on versions you think
   are ready, so you don't tune the task to it.
8. **Assume the solver attacks the task, and attack it first.** A rule in the instruction stops nobody, so build
   isolation into the environment: a separate verifier container, a solver that isn't root, and no network it doesn't
   need. Builders miss their own task's flaws, such as a shortcut that passes, a valid answer the verifier fails, or a tool
   that crashes. Give other agents the job of finding them. A flaw counts once it reproduces. Fix it in the
   environment, the task or the harness, and keep the task testing a skill worth having. Then add its kind to the
   exploit list in [integrity.md](references/integrity.md).
9. **A failure is a measurement or a bug, and a pass can be a shortcut.** A failed run either lacked the capability
   or met a broken task. Open the transcript of every pass and every fail before you believe a score, fix the bugs,
   and count only what's left.
10. **Every task earns its place.** It tests something new, or it adds an independent instance of a capability the set
    already tests, which narrows the set's interval. A near-copy that shares another task's data or answer only adds
    cost.

## Map of the skill

| File | Read it when you | It helps you decide |
|---|---|---|
| [ideation.md](references/ideation.md) | Have a question and no task yet | Which ideas are worth a rough version, and which kinds fall to a code tool in minutes |
| [example.md](references/example.md) | Want to see one task built from question to measurement | What each step produces |
| [improving-an-agent.md](references/improving-an-agent.md) | Build evals for your own agent | Which of its failures to turn into tasks |
| [environments.md](references/environments.md) | Plan the data, tools, services, simulated users or opponents | What the solver sees, can do and can check |
| [verifiers.md](references/verifiers.md) | Write the verifier and the instruction | How a pass is decided, and which stored cases prove it |
| [verifying.md](references/verifying.md) | Have a task folder to check | Whether it works through Harbor, with no model |
| [running.md](references/running.md) | Are about to run models | The exact `swarmory job harbor` or `harbor run` command, and how to rerun or regrade |
| [testing.md](references/testing.md) | Have run results to read | Whether a result is a measurement or a bug, and what to run next |
| [difficulty.md](references/difficulty.md) | See the strongest model pass | How to make the task harder for the right reason |
| [integrity.md](references/integrity.md) | Harden a task, or audit one for leaks and exploits | How a solver could cheat, and what stops it |
| [measuring.md](references/measuring.md) | Freeze a task, or report numbers | How many runs, and what the interval supports |
| [collaboration.md](references/collaboration.md) | Split the work, review a task, or revise after runs | Who checks what, and how to revise without tuning to the models |
| [frontier-notes.md](references/frontier-notes.md) | Want the evidence behind a rule | Whether a rule applies to your case |
| [templates/task/](templates/task) | Start a task | Copy it: a complete task with a verifier, stored cases and tests |
| [templates/idea-card.md](templates/idea-card.md) | Start an idea, and after each version | The task's record: its claim, each version and what each model did |
| [templates/](templates/) briefs | Hand an idea card or a task to a reviewer | Give the critic, auditor, reader or hacker its brief and nothing more |
| [scripts/](scripts/) | Check a task or read runs | `verify_task.py`, `lint_task.py`, `summarize_runs.py` and `probe_odds.py` |

## The usual order of work

1. **Pick the claim.** Fill in the idea card's claim, evidence and cheapest shortcut. Use
   [ideation.md](references/ideation.md) to choose among ideas.
2. **Build a rough version.** Copy [templates/task/](templates/task): one instance, a crude verifier, the simplest
   environment that could work ([environments.md](references/environments.md), [verifiers.md](references/verifiers.md)).
3. **Prove it with no model.** Run `verify_task.py free` ([verifying.md](references/verifying.md)). In a swarmory run,
   lint it, commit it, and run `swarmory job harbor` with `--agent oracle` and with `--agent nop` instead.
4. **Run models.** A cheap model finds bugs. Then run two or three models of different strength
   ([running.md](references/running.md)), and open every transcript ([testing.md](references/testing.md)). Write each
   version's results in the idea card.
5. **Make it harder.** While the strongest model passes, change the task for the right reason and go back to step 3
   ([difficulty.md](references/difficulty.md)).
6. **Let someone else break it.** Hand it to a reviewer with the hacker brief, and fix what reproduces
   ([integrity.md](references/integrity.md)).
7. **Freeze it and measure** with fresh runs ([measuring.md](references/measuring.md)).

## A task is ready when

- Its stored cases score what they should, including 0 for the empty answer and 1 for the reference run through the
  solver's own tools ([verifiers.md](references/verifiers.md#store-cases-that-prove-the-verifier-right)).
- No key, verifier or reference reaches the solver ([integrity.md](references/integrity.md)).
- The free checks pass on Modal ([verifying.md](references/verifying.md)). In a swarmory run: the lint passes, the
  oracle job scores 1 and the nop job scores 0.
- An auditor who didn't build it wrote a blind answer in the task's output format, and it scored 1 through the
  verifier, or the disagreement was settled with evidence and the key or verifier fixed.
- Every break someone found reproduces as a stored case that now scores what it should.
- The strongest model you ran fails some runs, for the reason the claim names. Until then the task is a draft. Build
  the next version instead of noting it for later.
- Every run counts as a pass, a fail or an infrastructure failure, with a reason.
- You can say what it adds over the closest existing task.

## Spend only what the run owner approved

A model run spends only what the run owner approved. In a swarmory run, that is the run itself: its jobs, within
their limits, and its hours. Otherwise it is a yes to one command you showed them, or to a
[run spec](templates/run-spec.md). Before a command outside that, show the command, the models, the number of runs
and the tokens a smoke run suggests they will use, and wait for a yes.
