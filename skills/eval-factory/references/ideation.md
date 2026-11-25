# Find ideas worth building

Turn a question into a few ideas worth a rough version, not one perfect idea.

## Turn the question into a claim

Write the claim in three parts:

- **Who.** The target model, such as the strongest frontier models, a cheaper model you're choosing between, or your
  own agent.
- **Does what.** One behavior a run can show or not show.
- **Under which conditions.** What makes the behavior hard, such as a plausible wrong explanation, missing
  information, a step that can't be undone, or an opponent that adapts.

"Is good at data analysis" is not a claim. "Finds the cause of a revenue drop when two causes overlap in time and the
data has unit mismatches between systems" is.

If a run with the capability and a run without it don't differ in something a script can read, the claim isn't
ready.

Write the one sentence a passing run demonstrates. "It knew the answer", "it followed all 40 formatting rules" and "it
processed 10,000 rows" reward recall, arbitrary rules and volume. "It noticed the obvious explanation didn't fit one
data point, and checked" or "it retried a failed call, then confirmed the side effect happened once" describe a
behavior you'd want in a good agent. Only that second kind is worth a rough version.

## Ask what an expert would do

Before you build a rough version, answer four questions about the real work that the idea comes from:

1. What would a top practitioner do here that the claim doesn't ask for yet? That step is often the capability worth
   testing.
2. What raw material would they start from? They start from a real repository, a raw log export or a primary source,
   not a summary that someone already cleaned. A cleaned input removes the judgment that the task should test.
3. Which methods and tools would they use? Give the solver the same tools, so that a failure measures judgment and not
   a missing tool.
4. What would show that the result is right, other than your word or the solver's? Your answer is the verifier. If
   your answer is "I would read it", the idea fails [principle 2](../SKILL.md#principles).

## Where ideas come from

Start from the best evidence you have:

1. **A failure you've seen.** A run where a model or your own agent got something wrong that matters. A reported
   failure with no run is a lead. A hunch is a guess, so test it with a rough version early.
2. **Real work that agents still fumble.** Tasks people do every day, with messy inputs, where an agent produces
   something that looks right and isn't.
3. **A check that's cheap because the environment can confirm it.** A diagnosis the service's own records settle, or
   an optimization scored on held-out inputs. Not a hidden rule recoverable from complete data. That is a puzzle, and
   a code tool solves it.
4. **A familiar problem with one rule changed.** Memory can't answer it, and the task stays easy to explain.
5. **Weak spots in existing benchmarks.** Things a benchmark claims to test but verifies badly.

Some genres look hard and aren't. Decoding a hidden code and standard puzzle types tend to fall quickly to a strong
model with a code tool. If a model with a code tool and no environment could pass in one script, it's a puzzle. Prefer
information that costs something to get, actions that can't be undone, and domains outside textbook vocabulary.

Before building, search briefly for an existing task that already measures the claim: the Harbor registry
(`harbor datasets list`), papers and repositories. If one exists, change what yours measures or drop it.

## Patterns that tend to work

**Diagnose with a decoy.** Example: a week of service logs where both a deploy and a config change come just before an
error spike. Only the config change explains errors on the two hosts that never received the deploy. The verifier
checks the named cause, and a stored decoy answer must score 0. Teaches: test the leading explanation against all the
evidence before committing.

**Attribute changes to their causes.** Example: a batch job's outputs changed after two config edits and a library
upgrade shipped the same day. The source, the old and new outputs, and the change list are available. In 3% of rows,
two of the changes interact. The solver names which change caused each class of difference, and the verifier checks
each attribution. Teaches: separate tangled causes instead of blaming the most visible one.

**Find the best compromise when constraints conflict.** Example: a conference schedule whose room sizes, speaker
conflicts and travel windows are infeasible as given, plus a priority order for which constraints may bend. The
verifier checks that the schedule is valid under the relaxed set and that no higher-priority constraint was relaxed
when a lower one would have done. Teaches: notice infeasibility and trade off by a stated rule, instead of forcing an
answer.

**Work against a service that fails.** Example: a support task where the first refund call goes through but returns a
timeout ([example.md](example.md)). The verifier counts refunds in the service's own ledger, and passes only exactly
one. Teaches: recover from errors without duplicating side effects.

**Find what you need, at a cost.** Example: answer a customer's eligibility question with 15 record lookups across
systems where the answer needs 6, and some records are stale, with timestamps that show it. The verifier checks the
answer. Teaches: decide what to look at and what to trust, instead of reading everything.

**Chain dependent steps.** Example: rename a database column, backfill it, and update its consumers, where one
consumer builds the column name at run time, so a text search misses it. Hidden tests exercise that consumer, and a
scope check confirms nothing else changed. Teaches: reason about what the code does, not only what it says.

**Improve a number.** Example: make a query twice as fast without changing its results. The verifier measures on
held-out data, repeats the timing until it's stable, and checks the results match. Set the bar well above run-to-run
noise, or score something deterministic such as rows scanned. Teaches: make real improvements instead of fitting a
visible test.

**Play against opponents.** Example: write a bot for a small strategy game, scored over many seeded games against a
panel of reference bots of different strengths. Teaches: plan against an adversary, not a fixed answer.

**Clarify before acting.** Example: a simulated user asks to "clean up the old accounts" when two definitions of "old"
are plausible and only one matches their intent. The verifier checks the final state, and the service's log shows no
delete before the clarifying turn. Teaches: notice ambiguity that matters, and resolve it before an irreversible step.

**Say it can't be done.** Example: asked for last quarter's churn by region, the solver finds the region field was only
added mid-quarter. The pass is an answer that says so, gives the figure for the period that can be computed, and names
the gap. The verifier checks the stated limitation and the partial figure. Teaches: stop at what the evidence supports
instead of inventing the rest.

## How you make the content decides what goes wrong

| Way | How it works | What usually goes wrong | Check early |
|---|---|---|---|
| Built from a known answer | Decide the answer, then build the question or environment around it. | A second valid answer nobody listed. | Give a model the key and ask it to find the evidence for it. |
| Expert-written question | Experts write questions that models fail. | Keeping only questions models fail also keeps questions with wrong keys. | A second expert re-derives each key independently. |
| Real-work brief | A short request from a colleague, with the difficulty in the files and systems. | Every model scores 0, and nobody can tell why. | Write a partial score before the first run. |
| Mechanism first | Specify the causes, then generate data from them. | The data doesn't show the cause. | Recover every target from the files alone. |
| Seeded environment | A script builds the environment from a seed. | Tasks that look different but test the same thing. | Run the target model on a rough version, and compare variants for real differences. |
| Stateful services | Mock services whose state the solver changes. | Doing nothing passes, or the verifier reads the wrong record. | Run the untouched starting state through the verifier. |
| Hidden scorer | A starting solution and a scorer on held-out inputs. | Fitting a visible score. | Show only a validation score. Score a do-nothing and a random solution. |
| Opponent panel | Fixed opponents over a fixed schedule of games. | A panel every model beats, or none does. | Check that weak and strong reference players score differently. |
| Audit of a benchmark | Take an existing set and close its holes. | Fixing one instance and missing the class. | Turn each fix into a test that fails before the fix. |

## Pick what to build first

Rank ideas by:

- evidence that the failure matters
- a verifier that's cheap and right
- room to get harder in ways that ask for more of the capability
- how fast you can build a rough version

Drop an idea on paper only for a reason you can check on paper: the answer is public, there's no good way to verify
it, a cheap trick clearly wins, or it duplicates an idea you already have. Don't drop an idea because it seems too
easy. A run of the target model decides that, and it costs less than the argument.

Write the survivors as short [idea cards](../templates/idea-card.md), and build rough versions of the best two or
three.
