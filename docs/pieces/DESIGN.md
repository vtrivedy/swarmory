# Pieces: make every coordination decision in swarmory a small file a researcher can swap

Every `file:line` below refers to commit `07932e6`. This document is self-contained: it replaces an earlier plugin proposal and folds in what five audits of that proposal found. The reasoning behind it, its history and its weak points are in [COMMENTARY.md](COMMENTARY.md).

## Summary

Swarmory decides about fifteen things on behalf of a team of agents: whom a message reaches, when a turn starts, what a task is, who may push where, what the starting prompt says, whether a model call is allowed, which workers run. Today each decision is a few lines buried in `team.py`, `runs.py`, `verbs.py`, `prompt.py`, `proxy.py` or `worker/prompt.md`. To change one you edit the core.

This issue moves each decision into a **piece**: one Python file of plainly named functions. The defaults are today's code, moved. A run's `team.toml` lists extra piece files. A listed piece that defines a function takes that decision over, or adds a vote, or adds an observer, depending on the function's name. There is no registration API, no context object, no middleware chain and no plugin package format. The loader is about 110 lines.

Three rules hold the design together:

1. **A name has a kind, and the kind is the composition rule.** A *choice* (`route`, `start_at`, `refs`, `asleep`, `model`, `render`) has exactly one owner. A *vote* (`may_call`, `may_spend`) is asked of every piece, and one refusal refuses. An *observer* (`apply`, `tick`, `prompt`, `news`, `me`, `left`) is called on every piece.
2. **Decisions are logged, state is a fold of the log.** A piece keeps state only through `apply(team, event)`. What a choice decided is written into the event it decided (a message's `recipients` and `audience`), so replay never asks a piece again.
3. **The kernel owns delivery.** Inboxes, read marks and turn mechanics stay as `tests/model/Delivery.tla` models them, unchanged. A piece that wants to hold a message leaves the recipient out now and posts a fresh message later. It never re-inserts an old one.

The plan is nine small PRs. The package grows by an estimated 650 to 750 lines (it is 6,747 today). A prototype of the riskiest part (loader, `route`, `audience`, `start_at`, the lock, per-member git refs) is in this branch (`swarmory/`), with its evidence in `docs/pieces/evidence/`; the existing suite passes on it with no test edited. [COMMENTARY.md](COMMENTARY.md) explains the reasoning, the history and the weak points.

## Motivation: the coordination structure is the experimental variable

Swarmory is used for frontier research on agent teams. In that work the thing under study is often the coordination itself: who hears what, who waits for whom, what unit of work the team shares, where code may land, how the budget is split. A researcher forms a hypothesis ("squads that sync every six hours waste less on chatter than one open room"), changes one thing, runs the same mission twice, and compares the two logs and ledgers.

That loop needs four properties, and each one drove a design decision here:

- **The edit is small and local.** One behavior is one function in one file. Changing when turns start is a ten-line file, not a fork of `team.py`.
- **The default is exactly today.** A baseline run and a run from before this change must be the same experiment. Defaults are literal moves of today's code, proved by golden tests.
- **A run carries the code that ran.** Piece source is snapshotted into the run's `config.json` with its hash, as `image.sh` is today (`cli.py:629-630`, `runs.py:394-396`). An export is enough to know what was tested.
- **Two runs are comparable from their records.** Every decision a listed piece makes that differs from the default is in the log, so `tools/compare.py` can count it and a reader can ask why a message did not arrive.

Whether the board is a list or a graph is not the point of this issue. It is one of the swappable pieces, and one of the worked examples.

## Baseline

- `uvx --python 3.12 --with-editable . pytest tests -q -p no:cacheprovider`: 51 passed, 1 failed (`tests/test_report.py::test_report_gathers_the_records_and_runs_the_harness_on_them`, at `tests/test_report.py:80`, in `tools/report.py`, not the core).
- `tests/e2e*.sh` not run (they make real model calls).
- `tests/model/Delivery.tla` checked with TLC by the audits; it is not run by the suite.

## The pieces

Derived from what each function in the code decides today, not from a list of hook points.

| Piece | What it decides | Today's code | Interface | Default |
|---|---|---|---|---|
| `mail` | Whom a new message is for, and who may ever read it | `Team.addressed` (`team.py:278-286`), called from `post` (`team.py:297`) | choice `route(team, draft) -> Route(to, audience=None, held=())` | `pieces/mail.py`: `addressed` moved. `audience` is always `None`. |
| `turns` | When an agent's next turn starts | `SETTLE`, `IDLE` (`team.py:26-27`), `Team.hold` and `Team.next` (`team.py:567-583`, `613-628`) | choice `start_at(team, turn) -> float or None` | `pieces/turns.py`: at once unless the agent waits; then `SETTLE` after the first message, or `IDLE` after it began to wait. |
| `board` | What a task is: its events, verbs, followers, and what it tells an agent | `STATUSES`, `activity` (`team.py:23`, `74-80`), the `issue`/`assign`/`move` branches of `apply` (`team.py:161-175`, `206-214`), `issues`/`issue`/`new`/`assign`/`move` (`team.py:460-513`), unassign on removal (`team.py:539-545`), their rows in `VERBS` (`verbs.py:467-485`), `prompt.md:48-50`, `72-80`, `109-117` | observers `apply`, `me`, `prompt`, `left`; tables `VERBS`, `THREAD` | `pieces/board.py`: the flat issue board, with today's bare event types so old logs replay. |
| `git` | Which refs a member may push and fetch, which branch its clone is on, and which commits a turn reports | `PRE_RECEIVE` (`runs.py:59-66`), `GIT_HTTP_EXPORT_ALL` (`server.py:255-256`), `prepare` (`supervisor.py:66-78`), `commits` and `news` (`verbs.py:297-311`), `prompt.md:82-107` | choice `refs(run, who) -> Refs(push, fetch, track, refused)`; observers `news`, `prompt` | `pieces/git.py`: push `refs/heads/main` and `refs/submit/*`, fetch everything, track `main`, today's refusal text. |
| `staffing` | Which agents' workers run now | `Run.lead` and `Run.wanted` (`runs.py:129-144`), `STAGGER` (`prompt.py:13`), "waiting for lead" (`runs.py:218`), the lead paragraph (`prompt.py:95-97`), `prompt.md:68-71` | choice `asleep(run, who, now) -> str or None` (the reason, shown by `status`); observer `prompt` | `pieces/staffing.py`: everyone but the first agent is asleep until it opens a thread or `STAGGER` seconds pass. |
| `models` | Whether a model call may be made, and which model serves it | `Proxy._over` and `reserve` (`proxy.py:264-298`), the model a request names (`proxy.py:327-333`) | vote `may_spend(run, call) -> str or None`; choice `model(run, call) -> str` | `pieces/models.py`: `_over` moved; `model` returns the model asked for. |
| `prompt` | The starting prompt, assembled from sections that pieces own | `prompt()` (`prompt.py:78-97`), `worker/prompt.md` | choice `render(run, member, sections) -> str`; every piece may define `prompt(run, member) -> {key: text}` | `pieces/prompt.py`: today's template, with the passages that describe another piece's behavior replaced by that piece's section. |
| `clock` | What the server says on its own as time passes | `Run.warn` (`runs.py:146-162`), called at `supervisor.py:118` | observer `tick(run, now)` | `pieces/clock.py`: the deadline and budget warnings, moved. |
| (no default) | Which member may call which verb | Only token kinds decide today: `policy.TABLE` (`policy.py:16-30`) lets any member call any team verb | vote `may_call(run, who, verb, req) -> str or None` | No default piece votes. `policy.authorize` stays the kernel's check of token kinds. |
| `Harness` | How one coding agent is launched, resumed and parsed | `harness/base.py:36` | The existing protocol, unchanged | Five built-ins and `Command` |
| `Backend` | Where workers run | `backends/base.py:27` | The existing protocol, unchanged | `local`, `modal` |

Three data classes carry the choices' answers, all in `control_plane/pieces.py`:

```python
@dataclass(frozen=True)
class Route:
    to: list               # whose inbox the message goes in now (today's `recipients`)
    audience: list = None  # who may ever read it. None: whoever may read its conversation, as today
    held: tuple = ()       # whom the piece left out for now. Logged on the event for the piece's own apply()

@dataclass(frozen=True)
class Refs:
    push: tuple            # shell patterns the pre-receive hook matches: ("refs/heads/main", "refs/submit/*")
    fetch: tuple = None    # refs or ref prefixes the member may fetch. None: all, as today
    track: str = "main"    # the branch its clone is on, and whose new commits its turns report
    refused: str = "Push to main only: this server refuses %s. Rebase your commits onto main and push main."

@dataclass(frozen=True)
class Call:
    who: str               # the member, or None for a job's trial
    job: dict              # {id, budget, tokens} for a job's trial, else None
    provider: str
    route: str
    model: str             # the model the request names
    mine: float            # dollars this member has spent, with its calls in flight
```

and two records the kernel hands to choices:

```python
Draft(conv, author, text, mentions, reply_to)     # what route() is asked about
Turn(who, now, since, mail, heard, end)           # what start_at() is asked about:
    # since: when the agent began to wait, or None if it did not; mail: the messages for it not yet shown;
    # heard: when this held call first saw mail, or None; end: when the held request ends
```

Fifteen function names (six choices, two votes, seven observers) and two tables (`VERBS`, `THREAD`) are the whole vocabulary. Each is justified by a default that needs it or by a worked example below; nothing else is added.

## The kernel and its invariants

The kernel is what must be shared and trusted. It stays in `control_plane/` and knows nothing about squads, leads, boards or budgets beyond calling the pieces.

**What is in it**

- The log: `append`, `apply`, `replay`, nonces (`team.py:51-71`, `117-191`, `273-276`).
- Members, tokens, principals, and the token-kind table (`policy.py`).
- Conversations: channels, DMs, and **threads** (a conversation with followers, which is what an issue's thread and its `subscribers` are today). `resolve`, `join`, `leave`, `send`, `read`, `who`, `channel` stay kernel verbs.
- Delivery: inboxes, read marks, the `delivered`/`turn`/`read`/`wait` events, `mark_read`, `undelivered`, the held-call tickets and bells, `inbox`, `wait`, `next`, `me` (`team.py:317-347`, `556-628`).
- The one read rule, `Team.may_read` (new, below).
- Files, traces, export, the viewer feed.
- Git transport and the pre-receive hook.
- The gateway's forwarding, pricing, ledger, reservations and provider pauses (`proxy.py:300-447`).
- The supervisor's pure `plan`, its tick, backends and harnesses.
- Jobs, wired by hand as today (see Out of scope).
- The piece loader and dispatcher.

**Invariants.** Each is something a piece cannot break, and the reason the kernel can stay small.

- **K1. The log is the truth.** All state, the pieces' included, is rebuilt by applying events in order. A piece's `apply` must be a pure function of the event and prior state. A choice's answer that matters later is in the event (`recipients`, `audience`, `held`, the `main` of a `turn`). Replay calls `apply` only: it never calls a choice, a vote or `tick`.
- **K2. Delivery is `Delivery.tla`, unchanged.** An item enters an inbox only as a message event's `recipients`, at that event's `seq`. Read marks are one increasing watermark per (member, conversation) (`team.py:176-178`). A delivery shows an oldest-first prefix. So a piece can decline to address a member now, and it can post a new message later; it cannot insert an old `seq` into an inbox. Two audits ran the alternative (a `release` event that fills inboxes with old messages) against the real `Team` and against TLC: the released message lands below the read mark and is lost, or behind newer mail and is trimmed or shown twice (`team.py:180`, `187`; reproductions in `docs/pieces/evidence/audit-repros/hold_release_demo.py`, `exp_team.py` and `tla/DeliveryHold.tla`).
- **K3. Who may read a message is fixed when it is posted.** `audience` is logged on the message event and never changes. For each member, a conversation is then the fixed subsequence of messages whose audience admits that member, in `seq` order, and the delivery model applies to that subsequence as it stands. One function, `Team.may_read(member, message)`, is used by every read path: `read`, `read --all`, an issue's thread, a file's download, the unread counts of `me` and `next`. The operator and the view token read everything, as today (`policy.py:19`).
- **K4. One writer, no I/O under the lock.** Events are appended only under `Team.lock`. A piece's `route`, `start_at`, `may_call` and `apply` run under it and must not block or touch the network. `tick` runs inside `Supervisor.lock` (`supervisor.py:86`, `109-128`) and takes `Team.lock` when it appends; the lock order is `Supervisor.lock`, then `Run.changes`, then `Team.lock`, and never the reverse. A verb that needs I/O or `Run.changes` declares `atomic=False`.
- **K5. The operator and the server are never subject to a listed piece.** A choice or a vote about a member is asked of listed pieces only when that member is an agent. For the operator, for the server's own posts (`SYSTEM = "swarmory"`, `team.py:28`, `522`) and for job and spend tokens, the default piece answers. The dispatcher enforces this, so a piece keyed on squads or roles never has to say what a principal with no squad may do.
- **K6. Git rules are enforced by git.** What a member may push is checked by the pre-receive hook from patterns the server puts in the hook's environment. What it may fetch is enforced by `transfer.hideRefs`. The server never decides a push by parsing the request body: a gzip-encoded body bypasses that parse today (see Pre-existing bugs).
- **K7. Piece code comes only from the operator's `start` request.** It is stored in `config.json` and compiled from there. It is never read from a path at run time and never from the run's repo, which agents push to.

## Composition model, and why

### The model

A piece is a module. The loader reads a fixed set of names from it. The name's kind decides how it combines with the same name in another piece.

| Kind | Names | Rule |
|---|---|---|
| Choice | `route`, `start_at`, `refs`, `asleep`, `model`, `render`; each verb name in `VERBS`; each key a `prompt()` returns | One owner. The default piece owns it. If one listed piece defines it, that piece owns it instead. If two listed pieces define it, `start` fails and names both. |
| Vote | `may_call`, `may_spend` | Every piece that defines it is asked, defaults first. The first reason returned refuses. Defaults cannot be outvoted. |
| Observer | `apply`, `tick`, `prompt`, `news`, `me`, `left`, `check` | Every piece that defines it is called, defaults first, then listed pieces in `team.toml` order. |

Default pieces always load. `drop = ["board"]` in `team.toml` removes one whole, which is how a piece that replaces a default's events and verbs (a different board) takes its place. A listed piece that wants the default's answer imports it and calls it:

```python
from swarmory.pieces import mail

def route(team, d):
    base = mail.route(team, d)          # an ordinary function call, not a chain
    ...
```

Writes happen in exactly three places: a verb handler, `tick`, and the kernel itself. Each appends events under `Team.lock`. There are no reactions fired from `append`: a piece that must act on another piece's event notes it in `apply` and acts on the next `tick`, at most five seconds later (`TICK`, `supervisor.py:30`).

### The alternatives, judged on the same criteria

| | (a) Slots: one class per slot | (b) Reducers and pure functions returning events | (c) Votes and unions everywhere | (d) Middleware chains with `next()` | Chosen: named functions with a fixed kind |
|---|---|---|---|---|---|
| Edit to change one behavior | Subclass or copy the slot's whole class: 170 lines of board to change one rule | Small, but only after everything is rewritten as effect data | Small for refusals, undefined for anything that returns a value | Small | One function in one file |
| Two experiments together | Only if they touch different slots. Squads spans four slots, so it is four files and blocks every other experiment on those slots | Good | Good for refusals. Two routes cannot be unioned or intersected with a meaning. Unioned grants cannot restrict: a squad's push list unioned with the default's `main` restricts nothing | Order decides. A squad's push whitelist ahead of the default refuses everything; a short-circuit hides the event from later handlers; one global order cannot suit every hook | Function-level ownership: squads and a review gate and budget caps load together. Two pieces that both own `route` are refused at `start` with a message that says to merge them |
| Replay determinism | Depends on discipline | Best | Depends on discipline | Unlogged holds and rewrites | Same as (b) for state (`apply`); choices are logged in the event they decide |
| Testability | Needs a constructed slot object | Best | Good | Needs the chain | Call the function with a real `Team` on a temp folder |
| Lines | About 250 for protocols and registry | 900 or more lines of `team.py`, `verbs.py`, `jobs.py` rewritten | About 200 | The earlier proposal: 3,000 to 3,500 | Loader about 110; total about 700 |
| Default stays byte-identical | Yes | Hard: every verb changes shape | Yes | Yes | Yes: defaults are moved code, called through one indirection |

Why not pure (b). Its two valuable properties are kept: state is a fold, and decisions are logged. Its third property, handlers that return effects as data, costs more than it gives here. A board change appends an event and then posts an activity line whose recipients depend on state the first event just changed (`team.py:301-305`: `change` appends, `apply` subscribes the author and assignees, then `post` computes `addressed`). Returning both as data needs an interpreter for posts that are resolved in order, and an answer computed after they apply. `next` and `wait` hold a request for up to 120 seconds on a condition variable and cannot be a pure function to events at all. The race the audits found in handler side effects (a check, then an act, outside the lock) is closed more cheaply: the lock becomes reentrant for its owner, and the server holds it across the votes and the verb they guard.

Why not pure (a). An experiment is usually cross-cutting. Isolated squads change routing, git refs, the prompt and a timer. Under slots that is four replaced classes, each a copy of a default. Under function ownership it is one 60-line file that defines `route`, `refs`, `tick`, `apply` and `prompt`.

Why not pure (c). A vote is the right rule exactly where the answer is yes or no. For a value there is no neutral way to merge two answers, and the audits showed the merged-grant case fails in the obvious example.

### What the prototype showed

In the prototype on this branch (demos in `docs/pieces/evidence/demo/`):

- `control_plane/pieces.py` is 80 lines. With `route` and `start_at` behind it, a reentrant lock, `audience` and `may_read`, the diff to the package is +99 and -24 lines. The suite gives the same 51 passed and 1 failed, with no test edited, including the test that monkeypatches `SETTLE` and `IDLE` and the one that asserts `head()` runs outside the lock (`tests/test_turns.py:29-51`).
- `docs/pieces/evidence/demo/squads.py` (52 lines) holds cross-squad mail and releases it as a digest. `docs/pieces/evidence/demo/demo.py` reruns the audit's four scenarios on it: nothing is readable while held by any path, each message reaches its member once, and a replay of the log gives equal inboxes, marks and piece state.
- `docs/pieces/evidence/demo/test_git.py` drives real `git http-backend`: per-member push patterns in the hook, per-member fetch through `hideRefs`, and a gzip-encoded push that is still refused. It also found that hidden refs alone do not hide objects (see Security).

## How a researcher swaps or edits a piece

### File layout

```
swarmory/control_plane/pieces.py    the loader, the kinds, Route, Refs, Call, Draft, Turn
swarmory/pieces/                    the defaults, one short file each:
    mail.py  turns.py  board.py  git.py  staffing.py  models.py  prompt.py  clock.py
swarmory/testing.py                 team(...) and run(...): a real Team or Run on a temp folder, no server, no model
examples/pieces/                    the worked examples below, each with a test
tools/compare.py  tools/why.py  tools/fork.py
```

A mission keeps its experiments beside its brief:

```
missions/parser/
    BRIEF.md
    team.toml                 the baseline
    variants/squads.toml      the same team, plus pieces
    pieces/squads.py
    pieces/test_squads.py
```

### team.toml

```toml
pieces = ["../pieces/squads.py", "../pieces/caps.py"]   # paths relative to this file
drop = []                                                # default pieces left out whole, e.g. ["board"]

[[agent]]
harness = "claude-code"
count = 2
tags = ["squad:red"]            # new: free labels on a member, for pieces to key on

[[agent]]
harness = "codex"
name = "cx-1"
tags = ["squad:blue", "envoy"]

[piece.squads]                  # a table per piece, defaults included: [piece.turns] idle = 60
every_hours = 6
open_minutes = 20
```

- `cli.read_team` (`cli.py:596-611`) refuses every key but `agent` and `jobs` today. It learns `pieces`, `drop` and `piece`, reads each listed file, and sends `{name, source}` with the `start` request, as it sends `image.sh` (`cli.py:629-630`). `swarmory start MISSION --toml PATH` picks a variant file.
- `runs.agents` (`runs.py:429`) accepts one new agent key, `tags`, a list of short strings, kept on the member and so logged in its `member` event.
- The server validates at `start`, dry run included: names are `[a-z][a-z0-9_]*` and unique; a listed name equal to a default's needs that default in `drop`; each source is at most 64 KB and compiles; two listed pieces do not own one choice; every `[piece.X]` table names a loaded piece and passes that piece's `check(cfg, team)`, which raises a one-line `ValueError`. A piece with no `check` accepts only an empty table. Parsing stays as strict as it is today.
- The run's `config.json` gets `pieces: [{name, sha256, source}]`, `drop` and `piece`. A piece reads its table with `team.cfg(NAME)`; the loader sets `NAME` in the module.
- The default pieces take their constants from their tables, so the commonest edits need no code: `[piece.turns] idle = 60`, `[piece.staffing] stagger = 0`, `[piece.clock] warn = 1200`.

`swarmory start MISSION --toml variants/squads.toml --dry-run` prints the ownership table, from the prototype:

```
route     squads  (in place of mail)
start_at  turns
refs      squads  (in place of git)
apply     board, squads
tick      clock, squads
prompt    mail, turns, board, git, staffing, squads
note      squads owns route; the prompt section `mail` is still the default's
```

The last line is a lint: a listed piece that takes over a choice but leaves that default's prompt section in place is probably telling agents the old rule.

### Edit, the short way

To change one behavior, write the one function. A team whose waiting agents wake on DMs only:

```python
# pieces/dm_only.py
from swarmory.pieces import turns

def start_at(team, t):
    if t.since is not None and not any(m["conv"].startswith("dm:") for m in t.mail):
        return None                      # waiting, and nothing direct: stay held
    return turns.start_at(team, t)

def prompt(run, member):
    return {"turns": "You work in turns. If you run `swarmory wait`, your next turn starts only when a teammate "
                     "DMs you.\n\n"}
```

To change a default in depth, copy its file from `swarmory/pieces/`, list the copy, and put the default in `drop`.

### Test

A piece is plain functions, so its test is plain pytest with no server and no model:

```python
from swarmory.testing import team

def test_a_channel_post_does_not_wake_a_waiting_agent(tmp_path):
    t = team(tmp_path, ["ada", "grace"], pieces=["pieces/dm_only.py"])
    t.wait("grace", {})
    t.send("ada", {"to": "general", "text": "@grace fyi"})
    assert t.next("grace", {"timeout": 0}) == {"done": False}
    t.send("ada", {"to": "grace", "text": "need you"})
    assert t.next("grace", {"timeout": 1})["done"]
```

`swarmory.testing.team` builds the `config.json` a run would have and returns the real `Team`. `swarmory.testing.run` returns a real `Run` with a stub backend, for `refs`, `asleep`, `tick` and `may_spend`. An end-to-end run with the fake harness works as today (`tests/fake_harness.py`), with `pieces` in the team it starts.

### Compare

```
swarmory start missions/parser --budget 20 --hours 3                               # baseline
swarmory start missions/parser --budget 20 --hours 3 --toml variants/squads.toml
swarmory export RUN_A out/ ; swarmory export RUN_B out/
uv run tools/compare.py out/RUN_A out/RUN_B
```

`tools/compare.py` already prints a column per run from the exports (`tools/compare.py:27-48`). It gains four rows: the pieces listed with their hashes, messages held, verb and spend refusals by piece, and piece errors.

### Replay

- **Faithful.** Loading a run folder replays its log through the kernel's `apply` and each piece's `apply`, with the piece source in that run's `config.json`. No choice is asked again, so editing a piece file on disk cannot change how an old run reads. An export carries `config.json`, so it carries the code (`runs.py:352`).
- **What would this piece have routed differently.** `tools/why.py EXPORT --with pieces/x.py` replays the log and, before applying each message event, asks the new `route` about it and prints the messages whose `to` or `audience` would differ. No model call. About 35 lines. `route` is the only choice that can be re-asked from a log alone.
- **Continue from a point under different rules.** `tools/fork.py` (worked example 6).

### Why did that happen

Decisions by default pieces are logged exactly as today, so a baseline log is unchanged. A listed piece's decisions add to the log:

- a message event carries `audience` and `held` when its route set them, and `route: NAME` when a listed piece owns `route`;
- a refused verb appends `{"type": "denied", "by", "verb", "piece", "reason"}`, an event for no one, as a `push` is (`team.py:548-552`). A refused model call appends the same with `"verb": "llm"`, once per member and piece until that member's next allowed call, since a harness retries;
- a rewritten model call's ledger row carries `asked`, the model the request named;
- a refused push is already a `push` event with `ok: false` and the hook's reason (`server.py:88`);
- a piece's own events (`squads/open`) are in the log under its name;
- a piece function that raises is recorded in memory (piece, function, error, time) and shown by `swarmory status`.

`tools/why.py EXPORT m12` prints one message's route: who got it, who may read it, who was held and by which piece. `tools/why.py EXPORT ada` lists what was refused to a member and what was held for it.

## Worked examples

Each lives in `examples/pieces/` with a test. The squads example's routing, hold, digest and replay ran on the prototype (`docs/pieces/evidence/demo/squads.py`), and its ref rules ran through real `git http-backend`. The other examples are written against the interfaces above, are not yet run, and become real in PR 8.

### 1. Isolated squads with a six-hour sync

Agents carry `squad:red` or `squad:blue` in their tags, and one per squad carries `envoy`. Between syncs a message reaches and is readable by the author's squad only. Every six hours a window opens for twenty minutes: each member gets one digest of what was held for it, cross-squad mail flows, and envoys may push `main`.

```python
"""examples/pieces/squads.py"""
from swarmory.control_plane.pieces import Refs, Route
from swarmory.control_plane.team import SYSTEM
from swarmory.pieces import git, mail

def squad(team, x):
    return next((t[6:] for t in team.members[x].get("tags", ()) if t.startswith("squad:")), None)

def state(team):
    return team.state.setdefault(NAME, {"open": None, "last": None, "held": {}})

def check(cfg, entries):
    if set(cfg) - {"every_hours", "open_minutes"}:
        raise ValueError("[piece.squads] takes every_hours and open_minutes")
    if not all(any(t.startswith("squad:") for t in e.get("tags", ())) for e in entries):
        raise ValueError("squads: give every [[agent]] a tag like squad:red")

def apply(team, ev):
    s = state(team)
    if ev["type"] == "squads/open":
        s.update(open=ev["until"], last=ev["at"], held={})
    elif ev["type"] == "squads/close":
        s["open"] = None
    elif ev["type"] == "message":
        for x in ev.get("held", ()):
            s["held"].setdefault(x, []).append(ev["id"])

def route(team, d):
    base, mine = mail.route(team, d), squad(team, d.author)
    if state(team)["open"]:
        return base
    near = [x for x in team.members if squad(team, x) in (mine, None)]   # the squad, and the operator
    return Route([x for x in base.to if x in near], audience=near, held=[x for x in base.to if x not in near])

def refs(run, who):
    g, opened = squad(run.team, who), state(run.team)["open"]
    envoy = "envoy" in run.team.members[who].get("tags", ())
    return Refs(push=(f"refs/heads/squad/{g}", "refs/submit/*") + (("refs/heads/main",) if envoy and opened else ()),
                fetch=("refs/heads/main", f"refs/heads/squad/{g}"), track=f"squad/{g}",
                refused="Push to your squad's branch: this server refuses %s from you.")

def tick(run, now):
    team, cfg = run.team, run.team.cfg(NAME)
    with team.lock:
        s = state(team)
        if s["open"] and now >= s["open"]:
            team.append({"type": "squads/close"})
        elif not s["open"] and now >= (s["last"] or run.config["created"]) + cfg.get("every_hours", 6) * 3600:
            held = {x: list(ids) for x, ids in s["held"].items()}
            team.append({"type": "squads/open", "until": now + cfg.get("open_minutes", 20) * 60})
            for x, ids in held.items():
                lines = ["[%s %s] %s: %s" % (i, m["conv"], m["author"], m["text"])
                         for i in ids for m in [team.messages[i]]]
                team.post("general", SYSTEM, "Sync is open. Held for you since the last one:\n" + "\n".join(lines),
                          None, None, route=Route([x], audience=[x]))

def prompt(run, member):
    g = squad(run.team, member["id"])
    return {"git": git.prompt(run, member)["git"].replace("main", f"squad/{g}"),
            "squads": f"## Your squad\n\nYou are in squad {g}. Until the next sync, only your squad reads what you "
                      "send, and mail to others is held and reaches them at the sync.\n\n"}
```

What makes it correct where the earlier design was not:

- Held mail is never put back. The original stays hidden from the held member for good (`audience`), and the sync posts a fresh message with a fresh `seq` that quotes it. Marks and inboxes are untouched, so `Delivery.tla` holds as it stands.
- Hidden means hidden on every path, because `may_read` is the only read rule. In the prototype a held DM gives an empty `inbox`, an empty `read --all` and no unread count.
- `tick` decides from logged state and logs its decision before acting, so a second tick, or a restart, does not open the window twice.
- The server's own `@all` warnings still reach everyone: `route` is not asked about `SYSTEM` (K5).

Limits, stated plainly. Issue titles and bodies are board data, not messages, so the default board is shared across squads; a squad-private board is a copy of `board.py` with an audience on each issue, about 20 changed lines. Channel names and rosters are visible to all. Isolation of files on disk holds on Modal, where a sandbox mounts only its own folder (`backends/modal.py:133`), and not on the local backend, where agents share a machine (`prompt.py:68-69`).

### 2. A lead, with review by another harness

The agent tagged `lead` opens and assigns issues. Workers DM only the lead and otherwise talk on issues. A worker with nothing addressed to it does not start turns, and its worker stops. An issue moves to `done` only after an agent of a different harness approves it.

```python
"""examples/pieces/lead_review.py"""
from collections import Counter
from swarmory.control_plane.verbs import WRITE, Param, Verb
from swarmory.pieces import board, staffing, turns

def lead(team):
    return next(x for x, m in team.members.items() if "lead" in m.get("tags", ()))

def state(team):
    return team.state.setdefault(NAME, {"asked": {}, "approved": set()})

def apply(team, ev):
    if ev["type"] == "review/asked":
        state(team)["asked"][ev["id"]] = ev["reviewer"]
    elif ev["type"] == "review/approved":
        state(team)["approved"].add(ev["id"])

def may_call(run, who, verb, req):
    team, boss = run.team, lead(run.team)
    if verb in ("new", "assign") and who != boss:
        return f"only the lead ({boss}) opens and assigns issues: ask in general"
    if verb == "send" and who != boss:
        try:
            conv = team.resolve(who, req.get("to"))
        except Exception:                             # not a conversation: the verb itself says so
            conv = ""
        if conv.startswith("dm:") and boss not in conv.split(":"):
            return f"DM the lead ({boss}); talk to teammates on the issue"
    if verb == "move" and req.get("status") == "done" and req.get("id") not in state(team)["approved"]:
        return f"{req.get('id')} needs a review first: run `swarmory review {req.get('id')}`"

def start_at(team, t):
    if t.who != lead(team) and t.since is not None and not t.mail:
        return None                                   # a waiting worker has no idle wake
    return turns.start_at(team, t)

def asleep(run, who, now):
    team = run.team
    if who != lead(team) and who in team.waiting and not team.undelivered(who):
        return "sleeping until written to"
    return staffing.asleep(run, who, now)

def review(run, who, req, gone):
    team, me = run.team, who.member
    with team.lock:
        i = board.find(team, req)
        others = [x for x, m in team.members.items() if team.agent(x) and not m.get("removed")
                  and m["harness"] != team.members[me]["harness"]]
        if not others:
            raise ValueError("no agent of another harness is on the team")
        load = Counter(state(team)["asked"].values())
        reviewer = min(others, key=lambda x: (load[x], x))
        team.append({"type": "review/asked", "id": i, "by": me, "reviewer": reviewer})
        team.post(i, me, f"@{reviewer} please review {i}: pull it and run it, then `swarmory approve {i}`, or say "
                         "here what is wrong.", None, None)
        return {"id": i, "reviewer": reviewer}

def approve(run, who, req, gone):
    team = run.team
    with team.lock:
        i = board.find(team, req)
        if state(team)["asked"].get(i) != who.member:
            raise ValueError(f"{i} was not sent to you for review")
        team.append({"type": "review/approved", "id": i, "by": who.member})
        team.post(i, who.member, f"approved {i}", None, None)
        return {"id": i}

ID = (Param("id", "string", "the issue", True),)
VERBS = [Verb("review", "run", "team", "Asks an agent of another harness to review an issue.", review,
              lambda r, req: f"Sent {r['id']} to {r['reviewer']} for review.", ID, WRITE),
         Verb("approve", "run", "team", "Approves an issue you were asked to review.", approve,
              lambda r, req: f"Approved {r['id']}.", ID, WRITE)]
```

- The vote on `move` and the `move` verb run under one hold of the team lock, so the approval it read is the approval in force when the issue moves. The review assignment is one locked verb with one event, so a retried request cannot assign two reviewers.
- Rerouting a worker's DM into a DM with the lead is not expressible, by choice: a piece can change who receives a message, not which conversation it is in. Refusing with a reason does the job and leaves nothing to explain in the log.
- Sleeping uses what exists: an agent whose `asleep` gives a reason is stopped by the supervisor's `plan` (`supervisor.py:48-63`) and started again when the reason goes, at most once per `RESTART` seconds (`supervisor.py:31`). Its conversation and clone survive because its HOME is on the run's volume (`backends/modal.py:133`). The cost is a restart prompt and a rerun of `setup.sh` on each wake (`worker/agent.py:243`), so this suits teams where most workers idle for long stretches.
- `review` and `approve` reach agents through the CLI because the CLI learns a run's verbs from the server (PR 3). Today its command table is fixed (`cli.py:97`, `416`), so a new verb would be unreachable.

### 3. A dependency-graph board with wake-on-ready

`drop = ["board"]`, `pieces = ["graph.py"]`. A task has `needs` and is ready when all of them are done. Finishing a task wakes the owners of the tasks it unblocks, and nobody else.

```python
"""examples/pieces/graph.py (the core; me(), prompt() and the task and claim verbs are as short)"""
import re
from swarmory.control_plane.pieces import Route
from swarmory.control_plane.team import SYSTEM

THREAD = re.compile(r"t\d+")            # ids this board's threads have; resolve() and name checks use it

def tasks(team):
    return team.state.setdefault(NAME, {})

def apply(team, ev):
    t = tasks(team)
    if ev["type"] == "graph/task":
        t[ev["id"]] = {"id": ev["id"], "title": ev["title"], "needs": ev["needs"], "owner": None, "done": False}
        team.threads[ev["id"]] = []
        team.follow(ev["id"], [ev["by"]])
    elif ev["type"] == "graph/claim":
        t[ev["id"]]["owner"] = ev["by"]
        team.follow(ev["id"], [ev["by"]])
    elif ev["type"] == "graph/done":
        t[ev["id"]]["done"] = True

def ready(team, task):
    return not task["done"] and all(tasks(team)[n]["done"] for n in task["needs"])

def done(run, who, req, gone):
    team = run.team
    with team.lock:
        t = tasks(team)
        task = t.get(req.get("id"))
        if not task or task["owner"] != who.member or not ready(team, task):
            raise ValueError("finish a ready task you own")
        team.append({"type": "graph/done", "id": task["id"], "by": who.member, "output": req.get("output")})
        for nxt in t.values():
            if task["id"] in nxt["needs"] and ready(team, nxt):
                team.post(nxt["id"], SYSTEM, f"{nxt['id']} is ready: {', '.join(nxt['needs'])} are done.",
                          None, None, route=Route(team.threads[nxt["id"]]))
        return {"task": dict(task)}
```

- The board is a piece like any other: its own events, its own verbs, its own section of the prompt under the keys the default board used (`board_commands`, `board`, `board_report`), and its own lines in `swarmory` (`me`).
- Waking is posting. The kernel has one way to start a waiting agent's turn, a message in its inbox, and the board uses it. No wake hook exists or is needed.
- The stagger still works: the default `staffing` asks whether any thread exists, not whether an issue does.
- "Not on every comment" is a `route` that addresses a task's followers only for the server's ready notice. It is five more lines in the same file.

### 4. A different turn policy

Batch delivery: a waiting agent's turn starts fifteen minutes after it began to wait if anything came, at once on a DM, and never on nothing.

```python
"""examples/pieces/batch.py"""
from swarmory.pieces import turns

def start_at(team, t):
    if t.since is None or any(m["conv"].startswith("dm:") for m in t.mail):
        return turns.start_at(team, t)
    return t.since + team.cfg(NAME).get("every", 900) if t.mail else None
```

A baton (one agent works at a time) is the same shape: `start_at` returns `t.now` for the holder of the baton and `None` for the rest, and the verb that passes the baton posts to the next holder. A turns piece owes the kernel one thing: it must eventually start a turn for an agent that has mail. `Delivery.tla` assumes that fairness, and a piece that never starts turns starves delivery by design.

### 5. Model routing and budget

A per-agent cap, and a cheaper model for agents tagged `reviewer`.

```python
"""examples/pieces/caps.py"""
def may_spend(run, call):
    cap = run.team.cfg(NAME).get("each")
    if cap and call.who and call.mine >= cap:
        return f"{call.who} has spent its ${cap:.2f} share of the run's budget."

def model(run, call):
    tags = run.team.members[call.who].get("tags", ())
    return run.team.cfg(NAME)["review_model"] if "reviewer" in tags else call.model
```

`may_spend` is a vote, so it stacks with the default run budget and with any other cap. `model` is a choice: the gateway rewrites the request's model before it looks up the provider and the price, and the existing translation path serves a model of another provider (`proxy.py:342-350`).

### 6. What the earlier design could not express

**An agent that spawns agents.** The operator's `add` already adds agents to a live run: a `member` event, a clone, a worker (`runs.py:180-189`, `verbs.py:379-387`). The addition is `Run.hire(entries, **extra)`, the body of the `add` verb moved onto `Run` so a piece can call it, with the backend handed to `Runs`. About 15 lines.

```python
"""examples/pieces/spawn.py"""
import threading
from swarmory.control_plane.verbs import WRITE, Param, Verb

ONE = threading.Lock()

def spawn(run, who, req, gone):
    me = run.team.members[who.member]
    with ONE:
        kids = [m for m in run.team.members.values() if m.get("parent") == me["id"] and not m.get("removed")]
        if len(kids) >= run.team.cfg(NAME).get("most", 2):
            raise ValueError("you have as many helpers as this run allows")
        [new] = run.hire([{"harness": me["harness"], "model": me["model"], "role": req["role"],
                           "tags": me.get("tags", [])}], parent=me["id"])
    return {"id": new["id"]}

VERBS = [Verb("spawn", "run", "team", "Adds an agent like you to the team, with the role you give it.", spawn,
              lambda r, req: f"Started {r['id']}.", (Param("role", "string", "what it is to do", True),), WRITE,
              atomic=False)]
```

It is `atomic=False` because `hire` takes `Run.changes` and validates against the backend (K4). The parent is in the new member's logged record, so a tree of agents can be read from the log, and `caps.py` can charge a child's spend to its parent.

**Forking a run at a log point.** A run is a folder, and a folder copied into `runs/` is served (`runs.py:1-4`, `518-525`). `tools/fork.py RUN_FOLDER --at SEQ --toml variants/x.toml [--hours H]` writes a new run folder: the log up to `SEQ` with every token digest replaced by a dead one, the ledger up to that event's time, the repo with each ref set to the value the log's `push` events give it at `SEQ`, the shared files, and a `config.json` with a new id, a new deadline and the variant's pieces. Run it N times with N variants to get N continuations of one shared history. About 80 lines, no kernel change. It restores what the team shares (log, board, chat, repo, ledger). It does not restore each agent's private conversation, which the harness keeps as files that cannot be rewound to a `seq`; forked agents start fresh conversations with their clone, the board and their undelivered mail as of the fork. Worth doing now: it is the cleanest controlled experiment the system can offer, and it costs nothing in the kernel. It depends on every push being logged, which is one reason the gzip bug below is fixed first.

**Fresh conversations as a variable (the last, optional PR).** How a worker lives is `worker/agent.py`'s loop, which runs on the agent's machine and resumes one conversation until it fails twice (`worker/agent.py:249-292`). The server cannot say "start this turn with a clean context" today. The minimal addition is one more choice, `fresh(team, who) -> bool` (default `False`): `next`'s answer carries it, `swarmory next` exits 5 instead of 0, and the worker sets the conversation aside as it already can (`worker/agent.py:186-199`) before the session. About 30 lines. It makes stateless workers and context reset per task testable. It is last because no other example needs it.

## Security and trust

**Where piece code comes from.** Only from the body of a `start` request, which needs the operator token (`policy.py:17`). The operator's CLI reads the files from the operator's disk. The server stores the source in `config.json` and compiles it from there on every load. Nothing reads piece code from `work.git`: agents push to it, and the brief is read from `main` (`prompt.py:47`), so loading code from there would let an agent rewrite the rules of its own run. `add`, `set` and `remove` cannot change a run's pieces. If a piece file sits inside the mission folder it is seeded to `main` and agents can read it; an edit they push changes nothing. Keep pieces outside the mission folder when agents should not see the mechanism.

**What piece code can do.** It runs in the server process, which holds the provider keys and every run. That is more than the operator token can do today, so the server accepts listed pieces only when started with `SWARMORY_PIECES=1`. The local server that `swarmory start` launches sets it; a Modal deployment sets it in the server's secret if its owner wants pieces. A piece is operator-trusted code: there is no sandbox, and none is planned here. A piece that raises fails the one request it was asked about with the server's existing 500 answer (`server.py:198-200`); a `tick` or `apply` that raises is caught, recorded and shown by `status`. Nothing is disabled automatically: a rule that switches itself off when an agent makes it slow or makes it fail is a rule an agent can switch off.

**The operator and the server.** K5: listed pieces are never asked about the operator, `SYSTEM`, or job and spend tokens. A piece posts as the server with an explicit `route=`, which skips `route`.

**Reading.** One predicate, `Team.may_read`: the operator reads everything; a DM between two others is hidden (`team.py:269-271`); a message with an `audience` is readable by its author and its audience. It replaces four inconsistent checks: `read --all` uses `hidden` where unread uses `sees` (`team.py:384`), an issue's thread checks nothing (`team.py:472`), a file is readable if it was shared in any conversation that is not a hidden DM (`team.py:426`), and anyone may join any channel (`team.py:444-455`). With `audience` on the message, joining a channel reveals nothing the member could not read before. Not gated: channel names and rosters, and board data.

**Pushing.** The pre-receive hook is the only enforcement. The server puts the member's `Refs.push` patterns and refusal text in the environment of `git http-backend`, which the hook inherits; the hook file is the same in every run and falls back to today's rule when the variables are absent, so runs made before this change behave as before. Verified through real `git http-backend`, including with a gzip-encoded request body.

**Fetching.** When `Refs.fetch` is not `None` the server sets `transfer.hideRefs` for the request through `GIT_CONFIG_COUNT` (hide `refs/`, then un-hide each allowed ref), which hides other refs from fetch and from a push's ref advertisement. Two more steps are required, both found by the prototype:

- Git protocol v2 serves any object by id, whether or not a ref that reaches it is hidden. The prototype fetched another squad's commit by its id with `hideRefs` set. For a member with restricted fetch the server must not forward the `Git-Protocol` header (`server.py:261-262`); under protocol v0 the same fetch fails with "Server does not allow request for unadvertised object".
- The server makes each agent's clone from the bare repo on disk (`supervisor.py:66-78`), which copies every branch. For a member with restricted fetch the clone must be `--single-branch` on `Refs.track`.

**Tokens, keys, egress.** Unchanged: digests only, re-minted per start, provider keys never leave the server, Modal egress allowlist, no force-push and no branch delete (`runs.py:540-542`).

## Pre-existing bugs to fix first

Each was run against `07932e6`.

1. **A gzip-encoded push is not logged.** `Handler.git` forwards `Content-Encoding` to `git http-backend`, which inflates the body (`server.py:259-260`), but `pushes()` parses the raw bytes (`server.py:277`). Rerun of `docs/pieces/evidence/audit-repros/test_gzip_push.py`: the ref moves, `pushes(gzip body)` returns `[]`, and no `push` event is appended. The viewer, `compare.py` and the fork tool all read `push` events. Fix: inflate a gzip body for the parse only. The hook already enforces, and must stay the only enforcement.
2. **Four read paths, four rules** (`team.py:384`, `426`, `444-455`, `472`). Harmless while every channel and issue is open to the whole team, and a hole the moment anything is hidden. Fix: `Team.may_read`, used everywhere, with no change in today's behavior.
3. **`tests/test_report.py` fails** at `tests/test_report.py:80` (the copy of `log.jsonl` under `records/server/` differs from the export's). In `tools/report.py`, not the core. Fix it so the suite is the proof for every later PR.
4. **Nothing pins the default.** The starting prompt, the turn prompt and the log a scripted session writes have no golden test. PR 1 adds them, generated at `07932e6`, because "defaults behave exactly like today" is otherwise a claim without a check.

Two hazards the design must not trip, already true today:

- `Team.lock` is not reentrant (`team.py:88`) and verbs run outside it (`server.py:229-237`). A check made before a verb can be stale when the verb runs.
- `Supervisor.tick` runs `run.warn` and every start and stop inside `Supervisor.lock` (`supervisor.py:109-128`). Network I/O added there stalls every run on the server.

## Plan

Each PR ends with the suite green and a stated proof. Line budgets are net lines in `swarmory/` unless marked; moved code counts as zero.

**PR 1. Fix first, and pin the default.** Budget: +40, tests +180.
- Fix bugs 1, 2 and 3.
- Golden tests generated at `07932e6`: the starting prompt for a lead and a non-lead on each backend; `news_text` and `me_text` for a fixed answer; the `log.jsonl` (without `at`) of a scripted session that sends, opens, assigns, moves, reads, waits and turns.
- Proof: 52 of 52; the goldens pass on `07932e6` plus this PR; the gzip test logs the push.

**PR 2. The loader, `route`, `start_at`, `audience`.** Budget: +290.
- `control_plane/pieces.py`: load, ownership, `choose`, `refused`, `every`, `table`, the data classes, the K5 rule.
- `pieces/mail.py` and `pieces/turns.py`: `addressed` and the settle and idle arithmetic, moved. `Team.next` becomes the hold loop that asks `start_at`. `wait` keeps its own ten-second burst hold in the kernel: it is the agent's blocking read, not a turn decision.
- `Team.post(..., route=None)`; `audience`, `held` and `route` on the message event; `may_read` honors `audience`; `Team.cfg`, `Team.state`, `Team.agent`.
- `team.toml` `pieces`, `drop`, `piece`; `--toml`; validation and `check` at `start`; the snapshot in `config.json`; the table in `--dry-run`; `SWARMORY_PIECES`.
- `swarmory/testing.py`.
- Proof: suite and goldens unchanged (tests edit only the module they monkeypatch `SETTLE` and `IDLE` on); the audit's scenarios A, B and D as tests with a holding piece, ported from `docs/pieces/evidence/demo/demo.py`; a replay test (equal inboxes, marks, piece state); two owners refused; the operator and `SYSTEM` never routed by a listed piece; TLC passes on the unchanged `Delivery.tla`.

**PR 3. Votes, verbs from pieces, and a CLI that finds them.** Budget: +110.
- The team lock becomes reentrant for its owner (a 15-line wrapper that keeps `locked()`, as in the prototype). `Verb.atomic` (default true): the server holds the lock across `may_call` and the verb. `wait`, `next`, `spend` and the job verbs are not atomic.
- `run.verbs` is the kernel's run verbs plus the pieces' `VERBS`; `Handler.verb` looks a run verb up there (`server.py:208`). A `denied` event for a listed piece's refusal.
- A `verbs` verb that lists a run's verbs. For a command not in `COMMANDS`, the CLI fetches the listing and parses by rule: required params are positional in order, a last string param takes the rest of the line, an array takes a comma list, other params are `--name VALUE`, booleans are flags, and a write carries a nonce. `swarmory help` appends the run's own verbs.
- `tags` on `[[agent]]`.
- Proof: a fixture piece's vote refuses a verb with its reason and logs `denied`; a threaded test shows a vote and its verb are atomic; a fixture piece's verb is called through the real CLI; the operator is not refused.

**PR 4. The board as a default piece.** Budget: +35 (about 210 lines move).
- Threads in the kernel: `Team.threads` (conversation to followers) and `Team.follow`; `resolve`, `join`, `leave` and `mail.route` use them; the name checks at `team.py:248`, `436` and `runs.py:452` use the pieces' `THREAD`.
- `pieces/board.py`: the issue events, verbs, views and text, with today's event types and today's JSON. Observers `me` and `left` (the sentence a removal's announcement adds, `runs.py:199-202`).
- Proof: suite and goldens unchanged; a log recorded before this PR replays to equal state; with `drop = ["board"]` and no replacement a run starts, `swarmory new` answers 404, and `swarmory` prints no issue lines.

**PR 5. Prompt sections, turn news, staffing, clock.** Budget: +90.
- `worker/prompt.md` keeps its text; the passages owned by a piece become `$mail_commands` (lines 44-47), `$board_commands` (48-50), `$turns` (53-58), `$mail` (60-62), `$staffing` (68-71), `$board` (72-80), `$git` (82-107) and `$board_report` (109-117), each supplied by that default piece's `prompt()`. Keys the template does not name render in listed order before "Know what persists". `render` is the default `prompt` piece's function.
- `news(run, who, turn)` and `me(run, who)` return `(data, lines)`; the kernel's text prints the lines where commits and issues are printed today. The JSON of `me`, `next` and `wait` gains one key, `lines`; every existing key keeps its value.
- `pieces/staffing.py`: `lead` and `asleep`; `Run.wanted` and `Run.status` ask it. `pieces/clock.py`: `warn` as `tick`; the supervisor calls every piece's `tick` where it calls `run.warn` (`supervisor.py:118`), each in its own `try`.
- Proof: the prompt goldens are byte-identical; `tests/test_server.py`'s stagger test passes; a fixture piece that owns the `git` key changes only that section; a fixture `tick` runs twice and appends once.

**PR 6. The git piece.** Budget: +75.
- `Refs`; the hook reads its patterns and refusal text from the environment with today's rule as the fallback; `transfer.hideRefs` and no protocol v2 when fetch is restricted; `prepare(repo, work, track, single)`; the server creates `refs/heads/TRACK` at `main` if it is missing; `Run.head(ref)`; the `turn` event's `main` is the head of the member's tracked ref; `commits` moves to `pieces/git.py` as `news`.
- Proof: `docs/pieces/evidence/demo/test_git.py` as a test (push by pattern, fetch by ref, fetch by id refused, gzip push refused); default runs produce the same refusal text and the same `push` events.

**PR 7. The models piece.** Budget: +45.
- `Proxy` takes two callables from its `Run`. `_over` moves to `pieces/models.py` as `may_spend` and is asked at both sites that ask it today (`proxy.py:304`, `289`), under `Proxy.lock`, for budgeted and unbudgeted runs alike. In-flight tickets record the member. `model` is applied after the request's model is parsed (`proxy.py:327`); the ledger row carries `asked` when it differs.
- Proof: `tests/test_proxy.py` unchanged; a fixture cap refuses one member and not another; a rewritten call is priced and logged as the served model with `asked`.

**PR 8. Examples, tools, and the playbook.** Budget: +20 in `swarmory/` (`Run.hire`); `examples/` about 400; `tools/` about 150; tests about 250.
- `examples/pieces/`: `squads.py`, `lead_review.py`, `graph.py`, `batch.py`, `caps.py`, `spawn.py`, each with a test that states its claims (a cross-squad DM wakes no one and is unreadable; the sync delivers one digest once; a blue push to red's branch is refused; a worker's `new` is refused; `move done` waits for an approval by another harness; finishing a task wakes only the owners it unblocks; a child agent is logged with its parent).
- `tools/compare.py` rows; `tools/why.py`; `tools/fork.py` with a test that a fork at `SEQ` loads to the state the original had at `SEQ` and the same ref heads.
- `skills/swarmory/playbooks/pieces.md` for the operator's coding agent: which name decides what, the three kinds, how to test, how to compare.
- Proof: the example tests; one end-to-end run with the fake harness on squads and one on the graph board; `tools/compare.py` on a baseline and a squads export shows the new rows.

**PR 9 (optional). `fresh`.** Budget: +30. As described under worked example 6. Proof: a fake-harness run where `fresh` is true starts each turn with the starting prompt and a new conversation.

**Total.** About +735 in `swarmory/` with PR 9, about +705 without, on 6,747. Outside the package: about 400 lines of examples, 150 of tools, and 900 of tests.

## Open questions

1. **A failing `apply`.** A piece's `apply` that raises leaves that piece's state behind the log. The plan records the error and carries on. Should `start --dry-run` also replay the piece over a recorded log as a smoke test?
2. **One write per verb.** A verb that appends two events (an issue, then its activity line, `team.py:303-304`) can be cut between them by a crash. Buffering a verb's events and writing them in one `write` and one `fsync` would close that and save a sync per board change. It is not needed by any example. Do it later, or never?
3. **Single-file pieces.** A piece is one file that may import the standard library and `swarmory`. Is a folder of files needed, and if so is concatenation at `start` enough?
4. **`help` after a drop.** The CLI's usage text is static (`cli.py:33-95`) and still lists `new`, `assign` and `move` when the board is dropped. Generate that part from the server's listing, or accept it?
5. **Board isolation.** Should the default board put an `audience` on issues so squads can be private without copying it? It would make example 1 complete at the cost of a board-level read rule.
6. **Fairness of `start_at`.** Should the kernel log a warning when an agent has had undelivered mail for longer than some bound, so a starving turns piece shows up in `status`?
7. **Jobs as a piece.** Jobs touches 46 lines in `control_plane/` outside `jobs.py`. Porting it needs three more names (extra token kinds, extra workers, an HTTP route). Worth it once a second feature needs any of them.
8. **`SWARMORY_PIECES` on by default?** Off is safer for a shared deployment; on is friendlier for a single user.

## Out of scope

- Reactions fired from `append`, middleware chains, a registration or context API, decision classes, a rules DSL in `team.toml`, entry-point packages, a `plugin` CLI, hot reload, dependency declarations between pieces, automatic disabling of slow or failing pieces. Each was considered and cut; none is needed by a default or an example.
- Channels and DMs as a piece. They stay in the kernel with threads; `route` and `audience` cover what the examples change about them.
- Rewriting which conversation a message is in.
- Sandboxing piece code, or one server process per run.
- Rewinding an agent's private conversation in a fork.
- Agent-side MCP, MCP tools for piece verbs, and any change to the `Harness` and `Backend` protocols.
- Incremental unread indexes. `me`, `news` and `read` scan every message under the lock (`team.py:370`, `591`, `385`); that is a millisecond at today's scale and a separate issue at 50 agents.
- Viewer work. Events the viewer does not know (`denied`, a piece's own) are in the feed and are not drawn.
