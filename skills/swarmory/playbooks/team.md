# A team of peers

One way for a team to work. Paste the section below into a mission's BRIEF.md, after the goal and its check, and
change what does not fit the mission. Give an agent a different job with its `role` in team.toml. Swarmory never
reads this file.

```markdown
## How we work

The goal is a project that works and does what this brief asks, on main. You and your teammates own this codebase,
and its design is yours to decide.

### Start

Run `swarmory`, `git log`, `swarmory issues`, and `swarmory read general`. If the team has a plan, take an open
issue from it. If there is no plan and no one is writing one, propose a short plan in general and file it as issues.

### Split the work

- Before you build an issue, assign yourself and move it to doing (`swarmory assign ID +me`, `swarmory move ID
  doing`), so that two people do not build the same thing. Take only an issue no one else is assigned to.
- File an issue for each piece of work or bug you find.
- Put findings and decisions on the issue. Your conversation can reset, and chat scrolls away.

### Keep main working

- Push small changes that work. Run the check before each push.
- If main is broken, fixing it comes first.
- Close an issue after you run the change end to end the way a user would, and say which commit closed it.

### Work with your teammates

- Review and test your teammates' commits, and build on them. Challenge a claim with evidence.
- When the right approach is unclear, try two options quickly and share what you measured.
- When something is unclear, choose the most reasonable option, note it on the issue, and continue.
- Message a teammate when something changes for them. Say what changed, where (file, commit, or issue), and what you
  need. Status goes on the issue. Skip messages that only acknowledge.
- When your push unblocks a teammate, @mention them, so a teammate who is waiting starts at once.

### When your issue is done

Run the whole project against this brief, find what is missing or broken, file it, and take it on. Take on hard
problems too, not only safe ones. Run `swarmory wait` only when nothing is left that you can do.
```
