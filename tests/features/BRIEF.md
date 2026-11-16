# Feature check: run exactly these commands

## Goal

This run tests swarmory itself, so there is nothing to design or build. Each of you runs the steps under your own name,
in order, then the steps under **Everyone**, and later **Phase two** when you're told to. `swarmory me` tells you your
name. A checker reads the run's records afterwards and looks for exactly what these steps produce, so copy each
command and each message as written. Fill in only what is in angle brackets.

If a command this brief gives you fails, post `failed <your id>: <the command>: <its error, in one line>` in general
and go on to the next step. Don't retry it more than once, and don't work around it: a failure is a result the checker
needs to see. Run no commands beyond the ones given, and report only theirs. Post every message in general unless a
step says otherwise. To post a command's output, pipe it to `swarmory send general -`, which reads the message from
stdin, as the steps show. Save downloads in your work folder, where the steps say: you may share a machine with the
others. Work on main, and make no branches.

## Ada

1. `swarmory new "feature check" --assign grace`, and note the issue id it prints, such as `i1`.
2. `swarmory send grace "please comment ok on <the issue id>"`.
3. `swarmory channel feature --about "the feature check"`, then `swarmory send feature "@linus please join and say hi"`.
4. End your turn. When Grace has commented on the issue, run `swarmory move <the issue id> done`.
5. Run `swarmory read grace --all --limit 1000 | grep -c "please comment ok"`, and post
   `found ada <the number it printed>`.

## Grace

1. Wait for Ada's message: end your turn until it comes.
2. `swarmory send <the issue id from her message> "ok"`.
3. Write the line `grace attached this` to `attached.txt` in your work folder, then
   `swarmory send general "attached file, @linus" --file attached.txt`.

## Linus

1. Wait for Ada's @mention in the channel feature: end your turn until it comes.
2. `swarmory join feature`, then `swarmory send feature "hi from linus"`.
3. When Grace's `attached file` message is in general (`swarmory read general`), download its file:
   `swarmory get <its file id, like f3> got/`. Then
   `(printf 'file linus '; python3 tools/sha.py got/attached.txt) | swarmory send general -`.

## Turing

1. Ask for these jobs, from the root of the repo, one command at a time:

   ```sh
   swarmory job harbor tasks/hello --agent oracle --to general
   swarmory job harbor tasks/hello --agent nop --to general
   swarmory job harbor tasks/hello --agent claude-code --model claude-haiku-5-5 --to general
   swarmory job harbor tasks/hello --agent claude-code --model openai/gpt-6.1-sol --budget 0.5 --to general
   swarmory job harbor tasks/hello --agent codex --model anthropic/claude-sonnet-5-5 --budget 0.5 --to general
   swarmory job harbor tasks/hello --agent claude-code --model claude-haiku-5-5 --budget 0.0001 --to general
   ```

2. Ask for one more, `swarmory job harbor tasks/hello --agent oracle --attempts 3 --to general`, and cancel it at once
   with `swarmory job cancel <its id>`.
3. Run `swarmory sleep` until `swarmory job list` shows no job as `queued` or `running`. The first job can take ten
   minutes.
4. `(echo "jobs turing:"; swarmory job list) | swarmory send general -`.
5. `(echo "results turing:"; swarmory results tasks/hello) | swarmory send general -`.
6. If the claude-code claude-haiku-5-5 job without --budget was queued, run `swarmory job show <its id>`. One of its
   lines names the job's artifacts, such as `swarmory get f9`. Run that command with `trace/` added at the end, then
   `python3 tools/trajectory.py trace/job-<that job's id>.tar.gz | swarmory send general -`.

## Everyone

1. `(echo "spend <your id>:"; swarmory spend) | swarmory send general -`.
2. `python3 tools/envcheck.py | swarmory send general -`. It prints short hashes of your secrets' values, never a value,
   so it is safe to post.
3. `python3 tools/egress.py | swarmory send general -`.
4. Write one line, `<your id> was here`, to `checks/<your id>.txt` (make the `checks` folder if it is not there).
   Commit it, then `git pull --rebase` and `git push` to main. A teammate may push first: if the push is rejected
   because main moved, run `git pull --rebase` and `git push` again, up to three times. That is the step working, not
   failing.
5. Post `done <your id>`. Then end your turn, and stay idle until you're told to start phase two.

## Phase two

Phase two starts when a message from the run's operator says `phase two`. Then:

1. Find Grace's `attached file` message in general
   (`swarmory read general --all --limit 1000 | grep "attached file"`), and download its file with
   `swarmory get <its file id> again/`.
2. `git fetch origin`, which reads main from the restarted server.
3. `echo "phase2 <your id> $(python3 tools/sha.py again/attached.txt) $(git ls-tree --name-only origin/main checks/ |
   wc -l | tr -d ' ')" | swarmory send general -`. Then end your turn and stay idle.
