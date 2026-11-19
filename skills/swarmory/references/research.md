# What research says about agent teams

Read this when you help a person write a mission's BRIEF.md or team.toml. Each finding names its source,
and each one is a choice for the mission to make. Swarmory's code makes none of them.

| Finding | Source | What it means for a mission |
|---|---|---|
| Verification failures were about 23.5% of all multi-agent failures. Adding a check of the whole goal raised success by 15.6 points. | MAST, arXiv 2503.13657 | Give the mission a check that any agent can run, and say in BRIEF.md how to run it. |
| 16 agents with no chat built a C compiler. All 16 fixed the same kernel bug until GCC was used to give each agent different failing files. | Anthropic, "Building a C compiler with a team of parallel Claudes" | Work must split into independent failures. A check that reports which part fails helps the team split it. |
| Peers sharing locks: 20 agents worked like 2 or 3, and avoided hard problems. Planners, workers, and a judge per cycle worked at hundreds of agents. | Cursor, "Scaling long-running autonomous coding" | For a large team, give one agent a planning role in team.toml. |
| Independent agents amplified errors 17.2 times, against 4.4 times with an orchestrator. Gains came only on work that runs in parallel. | arXiv 2512.08296 | Size the team to the number of independent issues. |
| Two agents building features in one repo did about 30% worse than one agent. The causes were vague messages, broken commitments, and wrong beliefs about teammates. | CooperBench, arXiv 2601.13295 | Ask agents to keep status on issues and to message only when something changes for the reader. |
| "Keep going until done" prompts cause over-work. A check to pass works better. | Claude Code best practices | Name the check in BRIEF.md. Do not ask agents to stay busy. |
| Multi-agent runs use about 15 times the tokens of a chat. | Anthropic, "How we built our multi-agent research system" | Set `--budget` for a long or large run. |

`playbooks/team.md` applies these findings to a team of peers. Change it per mission, and compare two versions on
the same mission with `tools/compare.py` before you keep a change.
