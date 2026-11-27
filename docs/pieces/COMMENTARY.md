# Pieces: commentary for reviewers

This is the companion to [DESIGN.md](DESIGN.md). DESIGN.md says what to build. This file says why: the goals, the ideas underneath, how the design got here, what the prototype in this PR does and does not prove, where the design is weakest, and which other directions deserve a serious look. It is written for an agent or a person who has to evaluate the proposal without having seen the conversation that produced it.

Everything below refers to commit `07932e6` unless it says otherwise.

## Contents

1. [What this PR is](#1-what-this-pr-is)
2. [The goal, stated precisely](#2-the-goal-stated-precisely)
3. [The underlying ideas](#3-the-underlying-ideas)
4. [How the design got here](#4-how-the-design-got-here)
5. [What the prototype contains and what it proves](#5-what-the-prototype-contains-and-what-it-proves)
6. [Where the design may not be right](#6-where-the-design-may-not-be-right)
7. [Other directions worth pursuing](#7-other-directions-worth-pursuing)
8. [How to evaluate this proposal](#8-how-to-evaluate-this-proposal)
9. [Evidence index](#9-evidence-index)
10. [Glossary](#10-glossary)

---

## 1. What this PR is

A draft, for discussion. It is not meant to merge as is.

It holds three things:

- **A design** ([DESIGN.md](DESIGN.md)): make every coordination decision swarmory makes about a team of agents a small, swappable file called a *piece*, with today's behavior as the default pieces. It ends in a nine-PR plan with line budgets and a stated proof per PR.
- **A prototype of the riskiest part of that design**, in `swarmory/`: the piece loader, two default pieces (`mail`, `turns`), the `route` and `start_at` choices, a per-message `audience` with one read rule, a reentrant team lock, `tags` on agents, and per-member push and fetch rules enforced by git. The existing suite passes on it with no test edited: 51 passed, and the 1 failure that was already there on `main` (`tests/test_report.py`, in `tools/report.py`).
- **Evidence** (`docs/pieces/evidence/`): reproductions of the bugs that killed an earlier design, a TLA+ model that shows one of them, and demos that run the prototype through the scenarios that matter.

The prototype is deliberately partial. It exists to test the parts of the design that could be wrong in ways reading cannot settle: whether a choice can be moved out of `team.py` without changing behavior, whether holding mail can be done without breaking the delivery invariants, and whether git can enforce per-member rules. Everything else in DESIGN.md is written against interfaces and has not run. Section 5 is exact about which is which.

## 2. The goal, stated precisely

Swarmory runs a team of coding agents (Claude Code, Codex, pi, and others) in sandboxes, and the agents coordinate only through one server: a chat, an issue board, a shared git repo, and a model gateway. The owner's goal for this work:

> A modular, plugin-based architecture that makes it easy for builders and researchers to cleanly and minimally swap out or edit pieces, to alter and study new behavior.

The context is frontier research on agent teams. There the coordination structure is often the variable under study, not a fixed detail of the tool: who hears what, who waits for whom, what unit of work the team shares, where code may land, how the budget is split, whether workers keep their context. A researcher forms a hypothesis ("squads that sync every six hours waste less on chatter than one open room"), changes one thing, runs the same mission twice, and compares.

Unpacked, that goal sets six requirements. Every decision in the design traces back to one of them.

| # | Requirement | What it rules out |
|---|---|---|
| R1 | **Small, local edits.** Changing one behavior is one function in one file, readable on one screen. | Forking `team.py`; subclassing a 170-line class to change one rule; a DSL to learn before writing anything. |
| R2 | **The default is exactly today.** A run with no pieces listed is the same experiment as a run before this change. | Rewrites that "should" behave the same; any default that is new code rather than moved code. |
| R3 | **Experiments combine.** Squads plus a review gate plus budget caps load together without one silently overriding another. | Order-dependent composition; one global plugin order; short-circuits that hide events from later code. |
| R4 | **A run carries the code that ran.** Replaying or exporting a run reproduces it, including its pieces. | Loading pieces from a path at run time; re-asking decisions on replay. |
| R5 | **Runs are comparable from their records.** Every non-default decision is in the log. | Unlogged refusals, rewrites or holds. |
| R6 | **No bloat.** The package is about 6,750 lines of tight code. The architecture should add hundreds of lines, not thousands, and every interface should be justified by a concrete use. | Machinery for third-party plugin authors who do not exist yet; generality no example needs. |

Two non-goals, both explicit:

- **Not a plugin marketplace.** The authors are the operator and the operator's coding agent. Packaging, versioning and distribution can come later if they are ever needed.
- **Not a sandbox for untrusted code.** Pieces are operator-trusted, like the operator's own CLI commands.

## 3. The underlying ideas

### 3.1 A decision is the unit of swapping, not a subsystem

The first question was what a researcher actually wants to swap. Reading the code function by function, swarmory makes about fifteen decisions on behalf of a team:

- whom a new message is for, and who may ever read it (`Team.addressed`, `team.py:278-286`; the read checks scattered at `team.py:269-271`, `307-315`, `384`, `426`, `472`);
- when an agent's next turn starts (`SETTLE`, `IDLE`, `Team.hold`, `Team.next`, `team.py:26-27`, `567-628`);
- what a task is and how it moves (`STATUSES`, the issue branches of `apply`, the board verbs, `team.py:23`, `161-175`, `460-513`);
- which refs a member may push and fetch, which branch its clone tracks, and which commits a turn reports (`PRE_RECEIVE`, `runs.py:59-66`; `GIT_HTTP_EXPORT_ALL`, `server.py:255-256`; `prepare`, `supervisor.py:66-78`; `commits`, `verbs.py:297-311`);
- which workers run now (`Run.lead`, `Run.wanted`, `STAGGER`, `runs.py:129-144`, `prompt.py:13`);
- whether a model call may be made, and which model serves it (`proxy.py:264-298`, `327-333`);
- what the starting prompt says (`prompt.py:78-97`, `worker/prompt.md`);
- what the server says on its own as time passes (`Run.warn`, `runs.py:146-162`);
- which member may call which verb (today only token kinds decide, `policy.py:16-30`).

Each of these is a few lines. Most are buried in a larger function. The design's first idea is that **each decision becomes a named function in a piece file**, and the default piece is the code that makes the decision today, moved. A researcher swaps a decision, not a subsystem.

This is why there is no "message board plugin" or "task board plugin" as such. Those were the units of an earlier design (section 4), and they turned out to be the wrong grain. A researcher who wants squads does not want a different chat system. They want a different answer to "whom is this message for" and "who may push here".

### 3.2 A name's kind is its composition rule

Once decisions are functions, two pieces will sometimes define the same one. The design makes the function's name decide what happens, with three kinds:

| Kind | Names | Rule | Why this rule |
|---|---|---|---|
| **Choice** | `route`, `start_at`, `refs`, `asleep`, `model`, `render`, each verb name, each prompt section key | One owner. The default owns it unless one listed piece defines it. Two listed pieces defining it is a start-time error that names both. | A choice returns a value. Two values (two routes, two turn times) have no neutral merge. Pretending otherwise (last wins, first wins, union) silently changes the experiment. |
| **Vote** | `may_call`, `may_spend` | Every piece is asked. The first refusal refuses. Defaults cannot be outvoted. | A yes/no question has a safe merge: deny wins. Budget caps and permission rules stack naturally. |
| **Observer** | `apply`, `tick`, `prompt`, `news`, `me`, `left`, `check` | Every piece is called, defaults first, then listed pieces in `team.toml` order. | These add state, text or periodic actions; they do not compete. |

The payoff is R3. Squads (owns `route`, `refs`; observes `apply`, `tick`, `prompt`), a lead with cross-harness review (votes `may_call`; adds verbs `review`, `approve`; observes `apply`) and per-agent caps (votes `may_spend`; owns `model`) all load together, because they define different names. Where two experiments really do compete for the same decision, the system says so at `start` instead of letting load order decide.

A listed piece that wants the default's answer imports the default and calls it as an ordinary function:

```python
from swarmory.pieces import mail

def route(team, d):
    base = mail.route(team, d)
    ...
```

That is the whole extension mechanism. There is no registration API, no context object, no `next()` chain, no decision classes beyond three small data classes for choices' answers (`Route`, `Refs`, `Call`).

### 3.3 The log is the truth, and decisions are written into the events they decide

Swarmory already replays a run's whole state from `log.jsonl` (`team.py:117-191`). The design keeps that and adds two rules:

- **A piece's state is a fold of the log.** A piece keeps state only through `apply(team, event)`, which must be a pure function of the event and prior state. Replay calls `apply` and nothing else.
- **A choice's answer is logged in the event it decided.** A message event carries `recipients` (as today), plus `audience` and `held` when a piece set them, plus `route: NAME` when a listed piece owns routing. Replay never asks a choice again.

The consequence is R4 and R5 together: editing a piece file cannot change how an old run reads, and every non-default decision is visible in the log for `compare.py` and `why.py`.

### 3.4 The kernel owns delivery, and pieces never rewind it

Swarmory's delivery logic (inboxes, read marks, turn boundaries, the held `next`/`wait` calls) is the most carefully built part of the system, and it has a TLA+ model (`tests/model/Delivery.tla`). Read marks are one increasing watermark per (member, conversation) (`team.py:176-178`).

The design keeps all of that in the kernel, unchanged, and gives pieces exactly two levers over it:

- decline to address a member now (`Route.to`), and
- post a fresh message later.

A piece can never put an old message into an inbox. Section 4 explains why: an earlier design did exactly that, and the audits showed that it loses messages or delivers them twice.

### 3.5 Who may read a message is fixed when it is posted

The second lever a piece has over messages is `Route.audience`: who may ever read this message. It is logged on the message and never changes. Every read path goes through one predicate, `Team.may_read(member, message)`.

Fixing the audience at post time is what makes holding mail compatible with the watermark. For each member, a conversation becomes a fixed subsequence of messages (those whose audience admits that member), and the delivery model applies to that subsequence unchanged. If visibility could change later, a message could appear below a member's read mark, and the watermark would skip it.

The cost is real and deliberate: a held message is never revealed. When a squad's sync window opens, the held member gets a fresh digest that quotes the held messages, not the originals. Section 6 discusses whether that is acceptable.

### 3.6 Git rules are enforced by git

What a member may push is checked by the `pre-receive` hook, from patterns the server puts in the hook's environment. What it may fetch is enforced by `transfer.hideRefs`. The server never decides a push by parsing the HTTP request body, because a gzip-encoded body bypasses that parse today (bug 1 in DESIGN.md, reproduced in `docs/pieces/evidence/audit-repros/test_gzip_push.py`).

The prototype found two more things git needs to really hide a branch: protocol v2 serves any object by id whether or not a ref to it is hidden, so the server must not forward the `Git-Protocol` header for a member with restricted fetch; and the server-made clone must be `--single-branch`, or it copies every branch onto the agent's disk.

### 3.7 Piece code comes from the operator, snapshotted into the run

`team.toml` lists piece files by path. The operator's CLI reads them and sends their source with the `start` request, the same way it sends `image.sh` today (`cli.py:629-630`). The server stores `{name, sha256, source}` in the run's `config.json` and compiles pieces from there on every load.

Three properties follow:

- **R4:** a run and its export carry the exact code that ran.
- **Security:** nothing is ever loaded from the run's git repo, which agents push to. The brief is read from `main` (`prompt.py:47`), so loading code from there would let an agent rewrite the rules of its own run.
- **A fast edit loop:** edit the file, start again. No package, no install, no server restart, no redeploy.

### 3.8 The operator and the server are never subject to a listed piece

Choices and votes about a member are asked of listed pieces only when that member is an agent. For the operator, for the server's own posts (`SYSTEM = "swarmory"`, `team.py:28`) and for job tokens, the default piece answers. Without this rule, every piece keyed on squads or roles would have to say what a principal with no squad may do, and the audits found that the obvious example plugin crashed on the server's own deadline warnings and held the operator's messages.

### 3.9 Defaults are moved code, pinned by golden tests

R2 is only a claim unless something checks it. PR 1 adds golden tests generated at `07932e6` (the starting prompt for a lead and a non-lead, the turn and status text for fixed inputs, and the log of a scripted session) and every later PR must leave them byte-identical. Moving code behind an interface is then provably a refactor, not a rewrite.

## 4. How the design got here

The history matters for evaluating the result, because several of its choices are reactions to concrete failures of an earlier design.

### 4.1 First design: plugins with eight middleware hooks

The first proposal (not in this PR) borrowed from two existing extension systems:

- **Pi** (the minimal coding agent): a small fixed loop, with extensions subscribing to named events whose handlers return contracts (block, transform, continue).
- **DeepSeek Harness** (built on the Cordis kernel): no privileged core; plugins provide and inject services, registrations are reversible effects, and dispatch includes a waterfall mode where a handler can skip `next()` to veto.

It proposed eight middleware hooks (`send`, `deliver`, `visible`, `wake`, `verbs`, `model`, `push`, `tick`), handlers of the form `fn(event, nxt)`, a plugin context object with about a dozen registration methods, the message board and the task board as plugins over a kernel of "streams", a declarative rules plugin, entry-point packages, a plugin CLI, and nine phases.

### 4.2 Five audits

Five independent reviewers each attacked one angle, against the real code, and several ran experiments:

| Angle | Main findings |
|---|---|
| Kernel boundary and API | Holding then releasing a message breaks the read watermark (a released message lands below the mark and is lost, or behind newer mail and is delivered twice). `visible` was hooked at the wrong function: `read --all`, issue views and files use different checks. Middleware is the wrong shape for most hooks. Proposed instead: reducers over the log plus pure decision functions with unioned grants. |
| Bloat and scope | The first design adds an estimated 3,000 to 3,500 lines to a 6,800-line package. Most of it served authors and features nobody had asked for. A minimal path of about 850 lines was sketched. |
| Power for users | Against 15 research patterns, 2 were cleanly expressible, 11 partly, 2 not at all (forking a run, agents spawning agents). Order-by-file composition fails whenever two plugins touch one hook. Plugin verbs were unreachable by agents, because the agent CLI has a hard-coded command table (`cli.py:97`, `416`). |
| Correctness and safety | Confirmed the message loss with a TLC counterexample. Found that a gzip-encoded push bypasses the server's push parsing today. The example plugin crashed on server-authored messages, and a fail-closed policy turned that into permanently lost deadline warnings. Auto-disabling slow plugins was an agent-triggerable way to switch rules off. |
| Developer experience | Entry-point packages are not discovered on Modal as swarmory deploys today (source is copied, not pip-installed). Adding one plugin would have silently dropped all defaults. The default `main_only` push rule plus a squads push rule would refuse every push. Path-based plugins snapshotted into the run were proposed instead. |

The message-loss finding was reproduced twice by separate reviewers, and once more independently while preparing this PR (`docs/pieces/evidence/audit-repros/hold_release_demo.py`). The TLA+ model with a naive Hold/Release added (`docs/pieces/evidence/audit-repros/tla/DeliveryHold.tla`) fails where the unmodified model passes.

### 4.3 The redesign

The redesign started again from the goal rather than from the first design's hook list. It derived the decisions from the code (section 3.1), considered four composition models (section 7.1 lists the ones rejected and why), and prototyped the riskiest parts before writing the plan. The result:

| | First design | This design |
|---|---|---|
| Unit of extension | Plugin with registrations via a context object | Plain functions in one file |
| Composition | Middleware chains, ordered by `team.toml` | By name kind: choice (one owner), vote (deny wins), observer (all) |
| Holding mail | Kernel Hold/Release of the original message | Leave the member out now, post a fresh message later |
| Visibility | A per-item `visible` hook, unlogged | `audience` fixed and logged at post time, one `may_read` |
| Boards | Message board and task board as plugins over a stream-kind kernel | Channels, DMs and threads stay in the kernel; the board is a default piece that can be dropped and replaced |
| Git | A push hook in front of `git http-backend` | Patterns passed to the real `pre-receive` hook; `hideRefs` for fetch |
| Loading | Entry-point packages | Paths in `team.toml`, source snapshotted into `config.json` |
| Size | About +3,000 to +3,500 lines | About +705 lines (+735 with the optional last PR) |

## 5. What the prototype contains and what it proves

### 5.1 Files changed in `swarmory/`

| File | Change |
|---|---|
| `control_plane/pieces.py` (new, 80 lines) | The loader: name kinds (`CHOICES`, `VOTES`, `EVERY`), default and listed pieces, one-owner check, `choose` (with the operator/server rule), `refused` (votes), `every` (observers), `table` (what `--dry-run` would print), the `Route` data class. |
| `pieces/mail.py`, `pieces/turns.py` (new, 25 lines) | `addressed` and the settle and idle arithmetic, moved out of `team.py` as the default `route` and `start_at`. |
| `control_plane/team.py` (+84 net) | A reentrant team lock, `Draft` and `Turn` records, `Team.post` asks the `route` choice and logs `audience`, `held` and `route`, `Team.may_read` as one read rule wired into the unread and read paths, `Team.next` asks `start_at`, pieces' `apply` called from `apply`. |
| `control_plane/runs.py` (+13 net) | The `pre-receive` hook reads push patterns from `SWARMORY_PUSH`, falling back to today's rule. `tags` accepted on `[[agent]]`. `Run.refs(who)`: a hard-coded prototype of the git piece keyed on a `squad:` tag. |
| `control_plane/server.py` (+6 net) | Passes push patterns and `hideRefs` to `git http-backend`; drops the `Git-Protocol` header when fetch is restricted. |

Not in the prototype: the `team.toml` and CLI wiring for `pieces`, `drop` and `[piece.X]` (pieces reach a run only through `config.json` today), votes wired into the server, verbs from pieces, CLI verb discovery, the board as a piece, prompt sections, the models piece, staffing, clock, `swarmory.testing`, and all tools. `Run.refs` is a stand-in, not the real git piece: it is keyed on a `squad:` tag and changes nothing for a run whose agents have no such tag.

### 5.2 What was run

| Claim | How it was checked | Result |
|---|---|---|
| Moving `route` and `start_at` behind pieces changes nothing by default | `pytest tests` on this branch, no test edited | 51 passed, 1 failed; the same failure as on `main` (`tests/test_report.py:80`, in `tools/report.py`) |
| Holding mail with a piece loses nothing and duplicates nothing | `docs/pieces/evidence/demo/demo.py` with `demo/squads.py` (52 lines), scenarios A, B, D from the audits | Nothing readable while held by any path; each message reaches its member once; the sync digest arrives once |
| Replay reproduces piece state | Same demo, scenario E | Equal inboxes, marks and piece state |
| The operator and the server bypass listed pieces | Same demo, scenario F | The server's `@all` reaches every member while squads are closed |
| Two pieces owning one choice are refused | Same demo, scenario G | `a and b both define route, which has one owner: put them in one piece` |
| Per-member push and fetch through real git | `docs/pieces/evidence/demo/test_git.py`, through `git http-backend`, with assertions | Push by pattern, fetch limited by ref, fetch of a hidden commit by id refused under protocol v0, gzip-encoded push still refused by the hook |
| The earlier Hold/Release design is broken | `audit-repros/hold_release_demo.py`, `audit-repros/exp_team.py`, `audit-repros/tla/DeliveryHold.tla` | Message lost below the mark; message delivered twice; TLC counterexample |
| A gzip push is not logged today | `audit-repros/test_gzip_push.py` | The ref moves, `pushes()` returns `[]`, no `push` event |

Scenario C (`read --all` path) was not rerun on the prototype as a separate scenario; scenario B covers `read --all` for a held DM.

`test_git.py` and `test_gzip_push.py` assert their results. `demo.py`, `hold_release_demo.py` and `exp_team.py` print each scenario's outcome for a reader to check against the claims above; turning them into asserting tests is part of PR 2 in DESIGN.md.

### 5.3 What has not been run

- The other five worked examples in DESIGN.md (lead with cross-harness review, dependency-graph board, batch turns, budget caps and model routing, spawning agents). They are written against the interfaces and become real in PR 8.
- TLC on the unchanged `Delivery.tla` against the prototype. The argument that the model still holds is in section 3.5 and DESIGN.md K3; it has not been mechanically checked with `audience` in the model.
- Any run on Modal. The git rules were tested through `git http-backend` locally; `--single-branch` clones and `hideRefs` on Modal are reasoned, not run.
- Performance under many agents. Unread scans are linear in messages under the lock (measured separately: about 47 ms for `news()` at 100 agents and 100,000 messages, under 1 ms at today's scale). The design does not change that and leaves indexes out of scope.

## 6. Where the design may not be right

This section is the most important one for an evaluator. Each item is a real weakness or an assumption that could fail, with what would show it and what to do instead.

### 6.1 One owner per choice

**The assumption.** Two independently written pieces will rarely both need to change the same choice, and when they do, merging them by hand into one file is acceptable.

**Why it might fail.** Routing is the most popular choice. Squads, gossip, debate turn order, blind review and a lead's routing all want `route`. A researcher who wants "squads plus gossip within a squad" must write a third file that combines them. That is honest (the combination is a new experiment with its own semantics), but it is friction exactly where researchers want to combine things.

**What would show it.** Count how many example pairs a researcher actually wants to combine that both own `route` or `start_at`.

**Alternatives.** (a) Choice combinators: let a piece declare `route = narrow(mail.route)` style composition, where `narrow` filters recipients and filters compose by intersection. This works for routes that only remove recipients, not for ones that add them. (b) Split `route` into `recipients` (a filter pipeline, intersecting) and `audience` (a filter pipeline, intersecting), which composes for every isolating pattern but not for gossip-style ones that add recipients. (c) Accept it and ship good examples of merged pieces.

### 6.2 A held message is never revealed

**The assumption.** Releasing held mail as a fresh digest is as good as revealing the original.

**Why it might fail.** Threads and replies lose their structure: a digest quotes held messages but the held member cannot reply in place to the original. Some patterns want delayed reveal of the exact message (sealed-bid debate, simultaneous reveal).

**What would show it.** Run the squads example and read the agents' behavior after a sync. If they struggle to connect the digest to the conversation, the digest form is costing something.

**Alternatives.** A per-member *delivery seq*: when a held message is released to a member, it gets a new sequence number for that member, and marks and inbox order compare against that. One audit proposed exactly this. It is more powerful and it changes the delivery model, so it needs `Delivery.tla` extended and TLC run before it is trusted. It is the right next step if digests prove too lossy.

### 6.3 Effects only in verbs and `tick`

**The assumption.** Pieces do not need to react to events immediately; acting on the next tick (at most 5 seconds later) is fine.

**Why it might fail.** A board that wakes dependents when a task finishes does it inside the `done` verb, which is fine. But a piece reacting to *another* piece's event (a tracker reacting to the board) waits for a tick. Five seconds is negligible for agents whose turns take minutes, but the shape forces some pieces to split their logic between `apply` and `tick`.

**Alternative.** Reactions fired from `append` under the lock, returning events (the reducer model in section 7.1). Cleaner semantics, harder to reason about for re-entrancy and ordering, and it costs a rewrite of how verbs append.

### 6.4 The reentrant lock and atomic verbs

**The change.** The team lock becomes reentrant for its owner, and the server holds it across a vote and the verb it guards, so a vote's check cannot go stale before the verb runs.

**The risk.** Reentrant locks hide accidental nesting. A verb that blocks on I/O while holding the lock now stalls every agent in the run. The design marks such verbs `atomic=False`, and the waiting verbs (`wait`, `next`) are excluded, but it relies on authors marking verbs correctly.

**What to check.** That every verb that does I/O (`spawn`, anything that touches the backend or `Run.changes`) is non-atomic, and that the lock order `Supervisor.lock`, then `Run.changes`, then `Team.lock` is never reversed. A lock-order assertion in debug mode would make this mechanical.

### 6.5 Piece code is unsandboxed in the server

Pieces run in the server process, which holds provider keys and every run. The design gates them behind `SWARMORY_PIECES=1` and treats them as operator code. That is fine for one person running their own server. It is not fine for a shared deployment where several people start runs, because one person's piece can read another's run.

**Alternatives.** One server process per run (the supervisor already names workers per run); or pieces in a subprocess with a narrow RPC (simpler isolation, but every choice becomes a round trip, and choices run under the team lock, so latency matters).

### 6.6 Static membership

Members gain `tags`, which pieces key on. Tags are fixed at `start` (or `add`). Patterns like rotating squads, promoting a reviewer, or bracket tournaments need labels that change during a run and are logged. The design does not add that; a piece can keep its own mutable labels in its own state through `apply`, which works but means every such piece reinvents it.

### 6.7 Jobs stays hand-wired

The Harbor jobs feature touches about 46 lines of `control_plane/` outside `jobs.py`. Porting it to a piece needs three more names (extra token kinds, extra workers, an HTTP route). The design defers this until a second feature needs any of those names. An evaluator who sees jobs as the main thing to get out of the core should weigh that.

### 6.8 Prompts are where behavior is explained, and they can drift

Each piece owns prompt sections, and `--dry-run` warns when a listed piece takes over a choice but leaves the default's section (which would tell agents the old rule). This is a lint, not a guarantee. An experiment whose rules agents are told inaccurately measures something other than intended. A reviewer should check the examples' prompt sections as carefully as their code.

### 6.9 The CLI's generic verb parsing

Agents reach piece verbs because the CLI fetches a run's verb listing and parses arguments by rule (required params positional, a last string takes the rest of the line, arrays as comma lists, others as `--name VALUE`). Generic parsing is less helpful than hand-written commands: worse errors, no custom help text, ambiguity in edge cases. It may be worth letting a verb declare its own usage line.

### 6.10 What is not covered at all

- Forking restores what the team shares (log, board, chat, repo, ledger), not each agent's private conversation, which the harness keeps as files that cannot be rewound to a log position. Forked agents start fresh conversations.
- Board data (issue titles and bodies) is not gated by `audience`; a squad-private board is a copy of the board piece with an audience on issues.
- Channel names and rosters are visible to all.
- File isolation on disk holds on Modal and not on the local backend, where agents share one machine.
- Unread scans remain linear (section 5.3).

## 7. Other directions worth pursuing

### 7.1 Composition models that were considered and rejected

The redesign compared four models on the same criteria before choosing. Each rejected model has a real strength, and a reviewer may weigh the criteria differently.

| Model | Strength | Why it was not chosen |
|---|---|---|
| **Slots**: one class per subsystem, swap the class | Clear boundaries, easy to document | Experiments are cross-cutting. Squads touches routing, git, prompts and a timer: four replaced classes, each a copy of a default, and it blocks any other experiment on those four slots. |
| **Reducers plus pure functions returning events** (effects as data) | Best replay determinism and testability; plugins can react to each other | Needs about 900 lines of `team.py`, `verbs.py` and `jobs.py` rewritten. Board changes append an event and then post a line whose recipients depend on state the first event changed, so effects need an interpreter. `next` and `wait` hold a request on a condition variable and are not functions to events. The design kept its two best properties (state as a fold, logged decisions) without the rewrite. |
| **Votes and unions everywhere** | Composition is always defined | Only works for yes/no answers. Two routes cannot be unioned or intersected with a meaning, and unioned grants cannot restrict (a squad's push list unioned with the default's `main` restricts nothing). |
| **Middleware chains with `next()`** (the first design) | Familiar; maximally flexible | Order fights order; short-circuits hide events; decisions go unlogged. The audits showed each failure concretely. |

The chosen model is a hybrid: votes where the answer is yes/no, single ownership where it is a value, broadcast where it is additive. **The strongest alternative is the reducer model.** If the project later needs plugins that react to each other's events in the same transaction, or wants replay to be fully mechanical, that is the direction to go, and this design does not block it: pieces' `apply` is already a reducer.

### 7.2 A declarative rules layer on top of pieces

Most patterns researchers want are configuration, not code: who may DM whom, which roles may call which verbs, which tags wake on what. A `rules` piece that reads tables from `team.toml` could cover them without writing Python. The design leaves this out because per-piece config tables already make parameters config, and an interpreter is about 200 lines plus a second language to maintain. Revisit it when three or more pieces repeat the same Python.

### 7.3 Out-of-process pieces

Pieces as separate processes (webhook or JSON-RPC) would give isolation, let pieces be written in any language, and survive a piece crash without touching the server. The cost is latency on choices that run under the team lock, and a protocol to version. A middle ground: in-process for choices and votes (fast, pure), out-of-process for observers and `tick` (slow, effectful). Worth exploring if pieces start doing network work (GitHub, model calls for digests).

### 7.4 Agent-authored pieces

The most interesting long-term direction for research. An agent inside a run could write a piece and propose it. The current design lets agents write piece files in the repo and propose them in an issue, but never load them; the operator reviews and installs for the next run. A tighter loop (a reviewed piece hot-loaded into the running run, logged as an event) would let a swarm change its own coordination as it learns. It needs unload semantics, a logged `piece/loaded` event, and a clear rule for which decisions already made stay made.

### 7.5 Richer delivery

Per-member delivery sequence numbers (section 6.2) would allow true delayed reveal, sealed bids and simultaneous release. This is the one direction that requires changing the delivery model, and it should start with `Delivery.tla`: extend the model, get TLC green, then write code.

### 7.6 Mutable, logged membership and labels

A kernel event for adding and removing labels on members (`label` events, policy-gated), plus `Run.hire` for pieces to add agents, would cover rotating roles, promotions, brackets and dynamic team growth. The design includes `Run.hire` (about 15 lines) and leaves labels to pieces. A shared kernel mechanism would stop each piece reinventing them.

### 7.7 Worker lifecycle as a piece

How a worker lives is `worker/agent.py`'s loop on the agent's machine. The design adds one optional choice, `fresh` (start a turn with a clean context). A fuller direction: pause at a turn boundary, snapshot and clone an agent's home for forks, and per-turn context policies. These are some of the most consequential variables for agent research (context management drives a lot of agent behavior) and they live mostly outside the server today.

### 7.8 Scale

If swarms grow past a few dozen agents, two things in the kernel come first: incremental unread indexes maintained in `apply` (replay-safe, since they are derived from the log), and batching `fsync` for message events. Neither changes the piece model.

## 8. How to evaluate this proposal

### 8.1 Run it

```sh
git checkout pieces-rfc
uvx --python 3.12 --with-editable . pytest tests -q -p no:cacheprovider        # expect 51 passed, 1 failed (test_report, as on main)

SWARMORY_REPO=. python3 docs/pieces/evidence/demo/demo.py                       # prints scenarios A, B, D, E, F, G
SWARMORY_REPO=. uvx --python 3.12 --with-editable . pytest docs/pieces/evidence/demo/test_git.py -q
```

The audit reproductions target the code before this branch. To see the bugs:

```sh
git worktree add /tmp/base 07932e6
SWARMORY_REPO=/tmp/base python3 docs/pieces/evidence/audit-repros/hold_release_demo.py
SWARMORY_REPO=/tmp/base uvx --python 3.12 --with-editable /tmp/base pytest docs/pieces/evidence/audit-repros/test_gzip_push.py -q
```

`docs/pieces/evidence/audit-repros/tla/` holds `Delivery.tla` (as in the repo) and `DeliveryHold.tla` (with a naive Hold and Release added). TLC is not bundled; with `tla2tools.jar`, `java -cp tla2tools.jar tlc2.TLC -config DeliveryHold.cfg DeliveryHold.tla` reports the counterexample.

### 8.2 Questions to answer

1. **Is a decision the right unit?** List the experiments you would run in the next three months. For each, which piece names would it define? If most need to own `route` together with another experiment, section 6.1 bites.
2. **Is single ownership acceptable?** Try writing two of the worked examples into one run. Did the start-time conflict check help or get in the way?
3. **Are digests acceptable for held mail?** Read section 6.2 and decide whether the experiments you care about need delayed reveal of originals.
4. **Is the kernel the right size?** Channels, DMs, threads, delivery and read rules stay in it. Is there an experiment you want that needs to change those, and not just route around them?
5. **Is the plan's first PR the right first PR?** It fixes three bugs and adds golden tests before moving anything. Check that the goldens cover what "behaves exactly like today" means to you.
6. **Line budget.** About +705 lines in the package across nine PRs. Does each interface in the pieces table earn its lines through a default or an example?
7. **Security posture.** Is `SWARMORY_PIECES=1` with operator-trusted code the right default for how swarmory is deployed?

### 8.3 Things to check in the code

- `Team.post` (`control_plane/team.py`): the route is asked once, under the lock, and its answer is logged. `route` is skipped for `quiet` activity lines and when the caller passes an explicit `route=`.
- `Team.may_read`: confirm every read path uses it. The prototype wires it into `unread`, `read --all` and the issue view; DESIGN.md PR 1 extends it to file downloads (`may_get`) and makes joining a channel reveal nothing a member could not already read.
- `Pieces.choose`: the operator/server rule is `agent=False` selecting the default owner.
- `PRE_RECEIVE` in `runs.py`: `set -f` prevents the patterns from globbing against the filesystem; the fallback keeps today's rule when `SWARMORY_PUSH` is unset.
- `server.py`: `GIT_CONFIG_COUNT` sets `uploadpack.hideRefs` per request; the `Git-Protocol` header is dropped only when fetch is restricted.

## 9. Evidence index

| Path | What it is | Runs against |
|---|---|---|
| `docs/pieces/evidence/demo/squads.py` | A 52-line squads piece: route with audience and held, digest on sync, ref rules | this branch |
| `docs/pieces/evidence/demo/demo.py` | Scenarios A (hold and digest), B (held DM unreadable by any path), D (no duplicate delivery), E (replay), F (server bypasses pieces), G (two owners refused) | this branch |
| `docs/pieces/evidence/demo/test_git.py` | Push patterns in the hook, fetch limited by `hideRefs`, hidden commit by id refused, gzip push refused | this branch |
| `docs/pieces/evidence/audit-repros/hold_release_demo.py` | The first design's Hold/Release: message lost below the mark, duplicate delivery, `read --all` leak | `07932e6` |
| `docs/pieces/evidence/audit-repros/exp_team.py` | Further hold and visibility experiments from the correctness audit | `07932e6` |
| `docs/pieces/evidence/audit-repros/test_gzip_push.py` | A gzip-encoded push moves the ref and logs no `push` event | `07932e6` |
| `docs/pieces/evidence/audit-repros/tla/` | `Delivery.tla` and `DeliveryHold.tla` with configs | TLC |

## 10. Glossary

- **Piece**: one Python file of plainly named functions that decides part of a run's behavior.
- **Default piece**: a piece shipped in `swarmory/pieces/` whose code is today's behavior, moved. Always loaded unless named in `drop`.
- **Listed piece**: a piece named by path in a run's `team.toml`, snapshotted into `config.json`.
- **Choice**: a name with one owner (`route`, `start_at`, `refs`, `asleep`, `model`, `render`, verb names, prompt section keys).
- **Vote**: a name every piece is asked about, where one refusal refuses (`may_call`, `may_spend`).
- **Observer**: a name every piece is called for (`apply`, `tick`, `prompt`, `news`, `me`, `left`, `check`).
- **Kernel**: what stays shared and trusted: the log and replay, members and tokens, conversations and delivery, `may_read`, git transport and hook, gateway forwarding and ledger, supervisor, backends, harnesses, the loader.
- **Audience**: who may ever read a message; logged on the message at post time and never changed.
- **Held**: members a route left out of a message's recipients for now; logged so the piece's own `apply` can track them.
- **Watermark / read mark**: the highest message seq a member has read in a conversation; everything at or below it counts as read.
- **K1 to K7**: the kernel invariants listed in DESIGN.md.
