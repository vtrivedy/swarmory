# Build evals for your own agent

Use this page when the question is "what do we want our agent to get better at?". The output is still Harbor tasks,
and the target model is your agent in its own harness.

## Find the failures that matter

Start from the best evidence you have:

- **Runs or logs of the agent.** For each failed run, read the whole run and note the first wrong step. Group the notes
  into failure modes named after what went wrong in your domain, such as "patched the caller instead of the shared
  helper", not generic labels like "reasoning error". Stop when new runs stop showing new modes. Pick the mode that is
  most common and costs the most.
- **Reports from users or the team.** A described pattern with no run attached is still a lead. Build a rough task
  that should bring it out, run the agent on it, and read what happens.
- **Nothing yet.** Write down where you expect the agent to fail, build a few rough tasks that vary along those
  dimensions, run the agent, and read the runs.

A log of the agent's answer is not ground truth. Build the key independently.

## Run your agent in Harbor

Wrap the agent as a Harbor agent class and run it with `-a <module>:<AgentClass>`. The class installs or starts the
agent in the task's container and passes the instruction in. The container has no network beyond the model endpoint,
so point the agent's tools at mock services inside the task
([environments.md](environments.md#services-and-simulated-users)).

## Turn a failure mode into tasks

Write the claim as for any eval ([ideation.md](ideation.md#turn-the-question-into-a-claim)), then design a situation
that brings the failure out reliably. A task the agent fails for another reason tells you nothing about the mode you
picked. Match the request to real use. If real users underspecify, the instruction underspecifies too.

## Aim where the agent sometimes succeeds

To measure where the agent stands, aim for tasks it passes some of the time. To test a change, a set it fails is useful
too, if a better agent could pass it. A task strong models find easy can still be the right test for your agent.

## Check that a change helped

Keep three fixed sets of tasks: one the agent fails for the target reason, one it already passes, and one that tests
the same capability in a different situation. Run the agent before and after the change on the same tasks, with the
same number of runs, and take the paired difference per set
([measuring.md](measuring.md#report-the-setup-the-interval-and-the-reason)). Keep the change if the failing set's
interval is above zero and the passing set's doesn't fall below zero. Otherwise add runs or tasks, or record
"no evidence of a difference". A gain on the failing set alone, with none on the third set, may mean the change fits
those tasks, not the capability.

## Harden tasks before anyone trains on them

Training pushes on every hole in the verifier, and a hole an eval can live with becomes a behavior the agent learns.
Reward hacking learned this way can spread to behavior well outside the task
([frontier-notes.md](frontier-notes.md#environments-and-practice)).

- Have a separate agent try to pass each task without the capability, using the
  [hacker brief](../templates/hacker-brief.md). Each find becomes an `exploit-<name>` stored case.
- Keep the tasks you report on apart from the tasks the agent trains on.
- For RL, a task teaches most when the agent passes about half the time. A common band is pass@1 between 0.25 and
  0.75 per task, from 16 to 64 attempts ([frontier-notes.md](frontier-notes.md#environments-and-practice)).
