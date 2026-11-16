Write the report of swarmory run $run, a team of coding agents on one mission. A person reads it to learn whether
the mission got done and how. You are not on the team. Read the run's records, change nothing, and write one page.

The records, in $records:
- BRIEF.md is the mission as the team got it, with its check.
- run.json names the team (id, harness, model, role), the operator (the person running the
  mission), when the run was created and its deadline (Unix seconds).
- server/log.jsonl has every team event, one JSON object a line, each with `seq` and `at` (Unix seconds):
  `message` (id mN; conv is a channel, an issue id iN, or dm:a:b; author, text, reply_to; `activity` true for the line a
  board change posts), `issue` (id iN, title, body, assignees, author), `assign`, `move` (status, note), `push` (by,
  ref, old, new, ok, reason) when present, `read` (member, conv, upto) and `woke` (member, upto), and for jobs
  `job` (id jN, by, params, commit, path, tree, budget, to), `claim`, `cancel` and `finish` (status, outcome:
  Harbor's headline, summary and error; artifacts: what the job wrote, as a file id). A message by `swarmory` is
  the server's, such as a job's result. Skip `member`.
- server/ledger.jsonl has one line per model call: who, model, and input, cached, written and output tokens, and
  `job` for a call a job's trial made.
- agents/ID/sessions/session-NNN.jsonl are each agent's transcripts in order. A transcript's first and last lines
  have `"type": "swarmory"`: the prompt the session got, and its exit status. agents/ID/agent.log, when present, says
  when each session started and how it ended.
- main/ is a clone of the team's main at the end. `git -C main log` is its history.

Rules:
- Write nothing but $report and files in main/. Never run `swarmory`, push, or contact the server or anyone on the
  team.
- Run the brief's check in main/. Report its command, its exit status and the output lines that decide it. If it
  can't run here, say why and mark the outcome inferred.
- Tie every claim to a record: a message id (m12), an issue id (i3), a commit sha (7 characters), a log seq, or a
  transcript line (agents/ada/sessions/session-002.jsonl:41). Mark each step recorded when the log, git or a check
  you ran shows it; arrived when a message reached an agent (a `woke` or `read` event, or the wake in its
  transcript) with no sign it acted; inferred when you drew it from several records. An agent saying it did
  something records the saying, not the doing. Drop any step you can't tie to a record.
- Give token counts from the ledger. No prices.

The page, in this order:
1. Outcome, at most five lines: whether the mission got done; main's head (sha and subject) and what it holds
   against the brief; the check's result there. Then one line with the team, the run's length, its token totals,
   and the path $records so a reader's own agent can check any claim.
2. Three to six cases. A case is a question a reader of this run would ask, such as "Why did i4 take two owners?",
   "Why were pushes refused?" or "Did anyone act on edsger's DM?". Pick the questions that explain the outcome.
   Each case has:
   - the question as its heading, and a short paragraph that answers it;
   - its steps, numbered, in time order. Each step gives who, what (sent, read, pushed, refused, moved, ran,
     wrote), when (seconds since the run's `created`), the record's id and file, a quote of the record in a line or
     two, and its mark: recorded, arrived or inferred;
   - a lane strip when the case spans two or more agents: inline SVG, one row per agent in the case, a time axis
     in seconds, each step a numbered dot on its agent's row that links to its step (id="c2-s3"). Draw an arrived
     dot dashed and an inferred dot hollow. Leave the strip out rather than let it grow past about 160px tall.
3. Open ends: what the brief asks for that main lacks, issues not done, refused pushes, failed sessions, each with
   its record. Leave the section out if there are none.

The file is self-contained: inline CSS and SVG, no scripts, no fonts, images or links that leave the file. Use the
system sans font and a monospace font for ids and code; text #1c1b19, secondary text #57554f, rules #e7e5df, links
#4a55d0, done #2c9566, failed #c0534b, one muted color per agent in the strips, white background, and a dark
version under `prefers-color-scheme: dark`. One column at most 860px wide. Plain sentences, sentence-case headings,
no badges or emoji.

When the file is written, print its path and nothing else.
