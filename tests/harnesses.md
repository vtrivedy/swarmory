# Testing the harnesses

`tests/e2e.sh` runs the harnesses in `E2E_TEAM` on the tiny mission in `tests/mission`, locally on the server's API
keys. The test kills one agent's worker and checks that she resumes her conversation. It then restarts the server. It
passes once every agent has pushed to main.

## Results

| Harness | `E2E_TEAM` | Gateway route | Result |
| --- | --- | --- | --- |
| every harness but antigravity | `claude-code:claude-haiku-5-5=1 codex:gpt-6-luna=1 pi:openai/gpt-6-luna=1 deepagents:anthropic/claude-haiku-5-5=1` | Anthropic Messages, OpenAI Responses | Pass, each agent pushed |
| pi, a team of one | `pi:anthropic/claude-haiku-5-5=1` | Anthropic Messages | Pass |
| claude-code | `claude-code:claude-haiku-5-5=2` | Anthropic Messages | Pass |
| codex | `codex:gpt-6-luna=2` | OpenAI Responses | Pass |
| pi | `pi:anthropic/claude-haiku-5-5=1 pi:openai/gpt-6-luna=1` | Anthropic Messages, OpenAI Responses | Pass |
| deepagents | `deepagents:anthropic/claude-haiku-5-5=1 deepagents:openai/gpt-6-luna=1` | Anthropic Messages, OpenAI Responses | Pass |
| deepagents | `deepagents:gemini/gemini-3.5-flash=2` | Gemini generateContent | Not finished. The key's free-tier quota stopped it after 12 calls. The gateway priced each call, and the agent resumed after the kill. |
| antigravity | `antigravity:gemini-3.8-flash-low=1`, with a claude-code agent | Gemini streamGenerateContent | Not finished. The free-tier quota, and then Google's 503 answers, stopped it. The gateway priced its call, it resumed after the kill, and a message reached it between model calls. |
| command (`run`, `resume`) | pi on `openai/gpt-5.4-mini`, from a team.toml removed when pi became built in | OpenAI Chat Completions | Pass |
| claude-code, codex, on subscriptions | `E2E_SUBSCRIPTION=1`, the default team | Anthropic Messages, OpenAI Responses | Pass, locally and on Modal. Since 2026-11-25 the test also checks that no agent obeys the mission's decoy AGENTS.md, CLAUDE.md and GEMINI.md, that each prompt holds the brief and the mission's skill, that the skill is linked for every coding agent, and that git shows no token. On Modal it checks no cache is on the Volume. |
| pi, deepagents | `pi:anthropic/claude-haiku-5-5=1 deepagents:openai/gpt-6-luna=1` | Anthropic Messages, OpenAI Responses | Pass. pi asks a Claude model for no thinking: Anthropic refuses pi 0.73.1's `thinking.type.enabled`. |

## Still to run

| What | How | Why |
| --- | --- | --- |
| Antigravity, through a pass | `E2E_TEAM="antigravity:gemini-3.8-flash-low=2"` with a paid-tier `GEMINI_API_KEY` and `agy` on PATH | The free tier allowed about 20 calls a day per model (observed), and gemini-3.8-flash often answered 503 |
| Modal, every harness | `tests/e2e.sh --modal` with pi, deepagents and antigravity in `E2E_TEAM` | Only claude-code and codex have run on Modal |
| A hold over 150 s on Modal | an agent that waits idle longer than `HOLD` | `next` holds at most 120 s per request, under Modal's 150 s limit; no run has waited that long |
| A long run | 4 agents for an hour on a real mission | Compaction, the fresh conversation after repeated failed sessions, and cost over time |

## Known limits

- pi 0.73.1 does not list today's models, so the pi harness declares the agent's model in `models.json` with `contextWindow`, `maxTokens`, and `reasoning` copied from LiteLLM's price table when present.

## What to look at after a run

- `swarmory status`: every agent has calls and a cost. An agent with sessions and no calls is crashing.
- Each agent's `sessions/*.err` in the data folder: the crash, when there is one.
- The run's `ledger.jsonl`: every call has `usd`, and its route matches the harness.
- The traces in the watch page: tools, results, replies, and usage, with no `other` events.
