# Raise difficulty in ways that matter

What a task makes hard is what agents learn to get good at, so make the hard part a behavior worth having. Set
difficulty from the target model's runs, never a cheap model's
([testing.md](testing.md#choose-the-target-model-and-the-cheap-model)).

## Harder, or just bigger

These make a task harder in a way that asks for better behavior:

- choosing between explanations that both fit most of the evidence
- noticing that something needed is missing or stale, and getting it
- checking a result before an action that can't be undone
- keeping track of state that changes during the task
- carrying a conclusion correctly through dependent steps
- recovering from a failure without repeating its side effects
- resolving an ambiguity that changes the outcome
- saying a request can't be done, and why, when it can't

Besides the size and clock that [principle 6](../SKILL.md#principles) names, these only add load. They make runs
longer and scores lower without telling you more:

- a long list of arbitrary rules
- hidden rules or trivia the solver can't discover
- unreadable inputs, such as heavy noise or tiny images, when perception isn't the claim

Length counts when steps depend on each other and an early error shows only late. Length made of repeats doesn't
([arXiv 2505.05115](https://arxiv.org/pdf/2505.05115)).

Before keeping a harder version, say which part of the claim it puts more weight on. If it needs a new sentence in the
claim, it's a new task.

## Turn one knob in steps

Change one condition per version. The underlying work and the verifier stay the same. A knob should change what must
be figured out, such as how close the decoy is or how many dependent steps there are. Noise, compression or missing
rows change how well the solver can see, which tests perception. Use them only when perception is the claim, and never
past readable.

Task: a customer's invoice total is wrong. Find the cause and correct the record. The knob is how close the decoy is
to the true cause.

| Version | What changes | What a pass requires |
|---|---|---|
| Easier | A discount was applied twice. Nothing else differs from the expected total. | Find the error. |
| Base | Also a 3-cent rounding difference on another line. | Rule out a difference by its size. |
| Harder | A loyalty discount was legitimately added the same week, within a dollar of the doubled one. | Check each discount against its authorization, not its size. |
| Hardest | The legitimate loyalty discount matches the doubled one to the cent, on the same day. | Rely on the authorization alone, since amount and date can't tell them apart. |
| Bigger, not harder | 5,000 invoices with the same single error. | Tests patience only. |

Other knobs suit the same task, and each tests a different part of a finance agent's job. Each is its own claim, with
its own easier and harder versions:

- The discount's authorization sits in the CRM, which must be queried. The solver has to notice the evidence is missing
  and get it before deciding.
- Correcting the invoice sends it to the customer, so it must be right before the write. That needs a way to check
  first, such as a preview endpoint, or passing is luck.
- The stored tax rate is stale, and the current one has to be fetched.

Keep an easier version that some model passes ([measuring.md](measuring.md#something-must-be-able-to-solve-it)), and
hold one harder version in reserve for when models improve.

When you're comparing models, aim for items that stronger models pass more often than weaker ones. When a weaker model
passes and a stronger one fails, read both runs before keeping the item. When the strongest model passes most runs, push
the knob further or find another kind of difficulty ([principle 7](../SKILL.md#principles)).

## Signs the difficulty is in the wrong place

- Failures cluster on reading the input. The task is testing perception.
- A short script solves it. It was only hard to do in one's head.
- More time or more tokens would fix the failures. The task tests endurance, unless efficiency is the claim.
- Passing runs are much shorter than failing ones, and failures stop at the first step. The solver misunderstood the
  task.

## Plant a decoy in diagnosis tasks

A decoy turns "reasons shallowly" into something the verifier can count. Make sure at least one piece of evidence holds
for the true cause and rules out the decoy. Without that piece, the decoy is a second valid answer. Add a `decoy_`
check to the verifier's record ([verifiers.md](verifiers.md#record-why-each-run-scored-what-it-did)).
