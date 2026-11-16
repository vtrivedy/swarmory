# Hello: two agents, one small module

## Goal

`greet.py`, at the root of the repo, with two working functions: `greet()`, written by Ada, and `shout()`, written by
Grace. Each of you checks the other's half before it counts as done. This mission is small on purpose: it shows that
a team can split work, push to one repo, and review each other through `swarmory`. It should take minutes.

## What to produce

`greet.py` already has both functions as stubs. Replace your stub's body, and leave the other one alone.

- `greet(name)` returns `"Hello, <name>!"` with `name` stripped of surrounding whitespace. If nothing is left, it
  returns `"Hello, world!"`.
  `greet("Ada")` is `"Hello, Ada!"`, `greet("  Grace ")` is `"Hello, Grace!"`, `greet("")` is `"Hello, world!"`.
- `shout(text)` strips surrounding whitespace, then any `.`, `!` and `?` at the end, uppercases what is left and adds
  one `"!"`. If nothing is left, it returns `""`.
  `shout("hello")` is `"HELLO!"`, `shout("ship it.")` is `"SHIP IT!"`, `shout("why?!")` is `"WHY!"`, `shout("  ")`
  is `""`.

Standard library only. No other files are needed.

## How to check it

`bash check.sh greet` checks `greet()` and `bash check.sh shout` checks `shout()` against the examples above.
`bash check.sh` checks both. Run your half's check before you push, and the whole check before you approve.

## How the work divides

1. Ada takes `greet()`, Grace takes `shout()`. Each of you opens an issue for your half (`swarmory new "greet()"
   --assign me`), writes it, runs its check, and pushes to main (`git pull --rebase` first: you both edit
   `greet.py`, in different functions).
2. When your half is pushed, assign your teammate to your issue and comment there asking for a review. Then end
   your turn: you'll be woken when they answer.
3. When you're asked to review, `git pull`, read the other half, run `bash check.sh`, and comment on that issue:
   `approved`, or exactly what is wrong. Fix what your reviewer finds.
4. Once your half is approved, move your issue to done, saying the commit. When both issues are done, the mission
   is finished: end your turn and stay idle.

If you are the only agent on the team (`swarmory who`), write both halves, run `bash check.sh`, push, and you are done.

## What else is here

`check.sh`, the check above. Nothing else.
