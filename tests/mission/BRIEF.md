# Hello: a small module, written as a team

## Goal

`greet.py`, at the root of the repo, with two working functions: `greet()`, written by Ada, and `shout()`, written by
Grace. Each of you reviews the other's half before it counts as done. The mission is small on purpose: it shows that
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

Standard library only. Try your function on the examples before you push.

## How the work divides

1. Ada takes `greet()` and Grace takes `shout()`. Each opens an issue for their half (`swarmory new "greet()"
   --assign me`), writes it, and pushes to main. Run `git pull --rebase` before you push, because you both edit
   `greet.py`.
2. When your half is pushed, assign your teammate to your issue and ask there for a review. Then run `swarmory wait`
   and end your turn. Swarmory wakes you when they answer.
3. When you are asked to review, `git pull`, try the other half on its examples, and comment on the issue:
   `approved`, or exactly what is wrong. Fix what your reviewer finds.
4. Once your half is approved, move your issue to done with a note that names the commit. When both issues are done, run
   `swarmory wait` and end your turn.

If you are the only agent on the team (`swarmory who`), write both halves and push.

An agent who is neither Ada nor Grace adds one line with their name to `team.txt`, pushes it, and reviews whichever
half is waiting. Run `git pull --rebase` before you push, and on a conflict in `team.txt` keep every line.
