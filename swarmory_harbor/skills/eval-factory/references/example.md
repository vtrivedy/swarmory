# A worked example, end to end

One task, from question to measurement. The details are invented.

## The question and the claim

A team's support agent sometimes refunds a customer twice. Its logs show the pattern: the payments API times out, the
agent retries, and both calls go through. The question is "what do we want our agent to get better at?".

- **Target model.** The team's support agent, in its own harness, with its production prompt and tools.
- **Claim.** When a refund call times out, the agent makes sure exactly one refund happens before telling the customer
  it's done.
- **With the capability.** After the timeout, it checks whether the refund went through, and retries only if it
  didn't. Its reply states the amount and when the money arrives.
- **Without it.** It retries blindly and refunds twice, or tells the customer the refund failed when it didn't.

A script can read the difference in the number of refunds in the payment service's ledger.

## The idea card

```
Claim: improve. After a refund call times out, the agent ensures exactly one refund before confirming.
Evidence: the ledger shows one refund, and the reply states $42.10 and a timeline.
Why it matters: production logs show duplicate refunds after timeouts.
Closest existing task: none found that checks idempotency after a committed timeout.
Cheapest way to pass without the capability: never retry. Fails, because the first call
  sometimes times out before committing.
v1: one order, a mock payments API, a ledger check. Results go here after each version.
```

## The environment

- A mock payments API runs as root inside the task container, with `POST /refunds` and `GET /refunds/status`. It
  writes every refund to a ledger file and every call to a call log. The solver reaches it only through the agent's
  normal payments tool.
- The scripted failure: the first `POST /refunds` commits the refund, then returns a 504 timeout. A second instance of
  the task uses the other case, where the first call times out before committing.
- An order database holds order 1182, charged twice for one $42.10 item, and nine unrelated orders.
- The ledger, the call log and the reply file are declared as artifacts, so they reach the verifier.

The task needs both timeout cases. With only the committed case, "never retry" would pass. With only the uncommitted
case, "always retry" would pass. Across both, only checking the status passes every time.

## The instruction

> Order 1182 was charged twice for the same $42.10 item. Refund the duplicate charge using your payments tools. Then
> write the customer a reply in `/app/output/reply.txt` that says the refund was issued, states its amount, and says
> when it will arrive. Don't change any other order. Only a fully correct result passes.

It states every rule the verifier checks. It doesn't mention timeouts, because noticing one is the capability.

## The verifier

Pass if and only if the ledger has exactly one new refund, of $42.10 on order 1182, nothing else changed, and the reply
says the refund was issued, its amount, and a timeline.

| Check | Tier | How |
|---|---|---|
| One new refund, on the right order for the right amount, and nothing else written | Code | `check_ledger` in [verifiers.md](verifiers.md#code-checks) |
| Reply states $42.10 | Code | A regex finds an amount in the reply, then `same_amount` compares it |
| Reply says the refund was issued, and when it arrives | Decision call | `decide` in [verifiers.md](verifiers.md#decision-calls-for-narrow-judgments), threshold set from 100 labeled replies |
| Asked for the status before any retry | Code, diagnostic | `path_checks`. It never changes the reward or the partial score. |

## The stored cases

| Stored case | Expected |
|---|---|
| Nothing done | 0 |
| The reference: status check after the timeout, one refund, a correct reply | 1 |
| A different valid run: retried only after the status showed no refund | 1 |
| Blind retry after a committed timeout, so two refunds | 0, failing `exactly_one_refund` |
| One refund, but the reply says the refund failed | 0, failing `says_issued` |
| The right refund, plus a refund on an unrelated order | 0, failing `exactly_one_refund` and `right_refund` |

## Testing it

1. **Cheap model, two runs.** Both runs stalled on the payments tool, because the mock API returned errors in a
   different shape from the real one. That's a bug in the environment, fixed by matching the real error shape. The next
   two runs worked, and one of them refunded twice.
2. **Target model, two runs.** One refunded twice, and one checked the status first. The task brings out the behavior
   the claim names, so it's worth hardening.
3. **Regrade.** One reply said "your refund is on its way" with no date. The decision call scored it as no timeline.
   "When it will arrive" in the instruction is clear, so the fail stands.

## Harder versions, one knob at a time

The knob is how much evidence the agent has about whether the first call went through:

| Version | What changes | What a pass requires |
|---|---|---|
| Easier | The 504 response says "refund may have been processed". | Read the error and check. |
| Base | A bare 504 with no body. | Know to check without a hint. |
| Harder | `GET /refunds/status` shows "pending" for the first 10 seconds, then the true state. | Wait and recheck, instead of retrying on "pending". |

A bigger version, such as 50 orders with the same timeout, is bigger, not harder
([principle 6](../SKILL.md#principles)).

## Measuring

The agent passed 6 of 10 frozen runs on the base version before a prompt change, and 9 of 10 after, on the same tasks
and seeds. The paired comparison over those runs has a wide interval, so the team added 20 more runs before deciding.
A task that tests the same capability in a different situation, a timeout on a subscription cancellation, moved the
same way.
