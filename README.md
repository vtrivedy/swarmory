<p align="center">
  <img src="assets/logo.svg" width="110" alt="swarmory">
</p>
<h1 align="center">swarmory</h1>
<p align="center"><b>swarm × factory</b></p>
<p align="center">A multi-agent swarm harness that works locally or in the cloud.</p>

<p align="center"><img src="assets/how-it-works.svg" width="820" alt="You talk to one server. It holds the chat and board, the team's git repo, the supervisor and the model gateway. Each agent runs in its own sandbox on Modal, or its own process locally. Agents exchange messages, pushes and model calls with the server, not with each other. The gateway adds your key and calls the model providers."></p>

- You write a brief. Swarmory starts a team of coding agents on it.
- Agents coordinate through a shared chat, an issue board, and one git repo.
- Each agent works in its own sandbox on Modal, or its own process on your machine.
- One server holds your keys, records the cost of every model call, and restarts an agent that dies.

<p align="center"><img src="assets/viewer.png" width="820" alt="The viewer on a run: the team's chat in the middle, the agents and channels on the left, spend and the board on the right, and a time bar at the bottom."></p>
<p align="center"><sub>The viewer. Left: channels and agents. Middle: the team's chat and pushes. Right: spend and the board. Bottom: drag to replay the run.</sub></p>

## Start a run

Install the CLI, and give your coding agent the skill that teaches it swarmory:

```sh
uv tool install git+https://github.com/vtrivedy/swarmory
npx skills add vtrivedy/swarmory --skill swarmory -g -y
```

Then ask your agent to run a swarm. The skill has it install what's missing, write the mission with you, and show you
a dry run before anything spends.

By hand, you need [uv](https://docs.astral.sh/uv/), git, and the command of each coding agent you use: `claude`,
`codex`, `agy` or `pi`. Deep Agents needs only uv.

```sh
npm install -g @anthropic-ai/claude-code           # claude, for claude-code agents
npm install -g @openai/codex                        # codex, for codex agents
```

```sh
export ANTHROPIC_API_KEY=...
swarmory start MISSION --team claude-code:claude-sonnet-5-5=2 --hours 0.5 --budget 5 --dry-run
swarmory start MISSION --team claude-code:claude-sonnet-5-5=2 --hours 0.5 --budget 5
```

`uv tool upgrade swarmory` updates the CLI. From a clone, `uv tool install .` installs it instead.

`MISSION` is a folder with a `BRIEF.md`, which says what to build and how to check it. It need not be a git repo. To
use your Claude or ChatGPT plan instead of an API key, see [Use a subscription](#use-a-subscription).

With `--dry-run`, the server checks the run as `start` does and prints the team, server and budget, but starts no run.
It checks that each key is set, not that it works. `start` prints the viewer link, which shows the whole run to anyone
who has it. Your id in the run is your first name from `git config user.name`.

The first `start` or `--dry-run` starts a local server in the background. It keeps running after runs end, with the
environment of the shell that started it. To give it a new key, stop it with `pkill -f '[s]warmory.cli serve'`, then
run `start` again.

## Pick the team

Each `--team` is `HARNESS[:MODEL]=COUNT`. Repeat it to mix agents.

```sh
swarmory start MISSION --budget 20 --team claude-code:claude-sonnet-5-5=4 --team codex:gpt-6.1-sol=4 --team pi:openai/gpt-6-luna=4
```

| Harness | Coding agent | Model |
|---|---|---|
| `claude-code` | Claude Code (`claude`) | Any model: `claude-sonnet-5-5`, `openai/gpt-6.1-sol` |
| `codex` | Codex (`codex`) | Any model: `gpt-6.1-sol`, `gemini/gemini-4-argon` |
| `antigravity` | Google Antigravity (`agy`) | A Gemini model `agy models` lists: `gemini-3.8-flash` |
| `pi` | [pi](https://github.com/badlogic/pi-mono) (`pi`) | `anthropic/...`, `openai/...` or `gemini/...`: `anthropic/claude-sonnet-5-5` |
| `deepagents` | LangChain's [Deep Agents](https://github.com/langchain-ai/deepagents) SDK, run with uv | `anthropic/...`, `openai/...` or `gemini/...`: `openai/gpt-6-luna` |

The server needs the key of each model's provider in `<PROVIDER>_API_KEY`, and prices calls from
[LiteLLM](https://github.com/BerriAI/litellm)'s table. With a budget, name each agent's model: the dry run refuses one
the table doesn't price.

For names, roles, and harness flags, put a `team.toml` in the mission folder instead:

```toml
[[agent]]
harness = "claude-code"
model = "claude-sonnet-5-5"
name = "lead"
role = "Split the work into issues and review every push."
args = ["--effort", "high"]

[[agent]]
harness = "codex"
model = "gpt-6.1-sol"
count = 5
args = ["-c", "model_reasoning_effort=high"]
```

To add a coding agent that swarmory doesn't build in, give its team.toml entry the `run` and `resume` commands that
run it headless. Each must contain `{prompt}`, and swarmory fills in `{prompt}`, `{model}`, `{provider}` and
`{model_id}`. Its model is
`anthropic/...`, `openai/...` or `gemini/...`, and its command must be on the server's PATH. The gateway serves
Anthropic's Messages API, OpenAI's Responses and Chat Completions APIs, and Gemini's API. The session gets the
gateway's address and the agent's run token, which the gateway takes in place of a key, in `ANTHROPIC_BASE_URL`,
`ANTHROPIC_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `GOOGLE_GEMINI_BASE_URL` and `GEMINI_API_KEY`. The optional
`setup` command runs in a shell before each session, with the same variables. Use it to write a config file when the
agent reads none of those variables. Such an agent sees messages only at its next turn, and the viewer shows its output
as text.

```toml
[[agent]]
harness = "my-agent"
model = "anthropic/claude-sonnet-5-5"
run = "my-agent --headless --model {model} {prompt}"
resume = "my-agent --headless --continue --model {model} {prompt}"
setup = "sh my-agent-config.sh"   # a file of the mission
```

| Option | What it does |
|---|---|
| `--hours H` | How long the run lasts. The default is 3. |
| `--budget USD` | Required. The server refuses model calls past this spend, at list prices. `none` sets no limit. With a budget, every model must be in LiteLLM's price table, which the dry run checks. |
| `--dry-run` | Check the run and print it, but start nothing. |
| `--subscription` | Use your Claude or ChatGPT plan instead of API keys. |
| `--grant NAME` | Give every agent an environment variable, or a Modal secret. |
| `--allow HOST` | On Modal, let agents reach `HOST` too. |
| `--jobs` | Let agents run [Harbor](https://github.com/laude-institute/harbor) evals on what they commit, as `jobs = true` in the mission's team.toml does. [`missions/eval-factory`](missions/eval-factory) is a mission that builds such evals. |

## How agents work

- An agent works in turns, and each turn resumes its conversation. When a turn ends, the next starts right away and
  opens with what changed: messages for the agent, unread channels, new commits on main, and the time left.
- An agent with nothing to do runs `swarmory wait`. Its next turn then starts when a message for it arrives (a DM, a
  reply, an @mention, a comment on an issue it follows, a job's result), or after 4 minutes.
- Messages for a built-in agent also reach it between its tool calls, through a hook.

Each agent's starting prompt is in the server's data at `runs/RUN/members/ID/prompt.md`. It opens with BRIEF.md,
whole, then explains the agent's machine, the `swarmory` command, the mission's skills, and a default way to work
as a team: issues, claims, small commits pushed often, and review. The brief wins over the default. To change how the team works,
write it in BRIEF.md under "How we work", and give agents roles in `team.toml`.
[`skills/swarmory/playbooks/team.md`](skills/swarmory/playbooks/team.md) is a starting point.

## Work with the team

| Command | What it does |
|---|---|
| `swarmory read` | Unread messages. |
| `swarmory send ada "status?"` | Message an agent, or `general` for everyone. |
| `swarmory status` | Time left, cost per agent, and the viewer link. |
| `swarmory set --budget 20` | Change the run's budget. |
| `swarmory add claude-code:claude-sonnet-5-5=2` | Add agents. They start at once. |
| `swarmory remove ada` | Take an agent off the team. Its open issues are unassigned. |
| `swarmory stop` | Stop the run. |
| `swarmory help` | Every command. |

The team's work is a git repo. On a local server, `git clone ~/.swarmory/data/runs/RUN/work.git`. On any server,
`swarmory export RUN` writes the run's records to `./RUN`, and `git clone RUN/work.bundle` gets the code.

## Use a subscription

```sh
export CLAUDE_CODE_OAUTH_TOKEN=...
export CODEX_AUTH_JSON="$(cat ~/.codex/auth.json)"
swarmory start MISSION --team claude-code:claude-sonnet-5-5=2 --team codex:gpt-6.1-sol=2 --budget 5 --subscription
```

`claude setup-token` prints the first value. On Modal, put both in the `swarmory-server` secret instead. The server
never refreshes the ChatGPT login, so run any `codex` command just before `start`, and on Modal put the fresh
`auth.json` in the secret again. On a plan, `claude-code` agents use Anthropic models and `codex` agents OpenAI models,
and they share the plan's usage limits with your own use. `--budget` still caps the run at list prices.

## Run on Modal

```sh
uv tool install 'swarmory[modal] @ git+https://github.com/vtrivedy/swarmory'
uvx modal setup                    # once, if you have no Modal login
TOKEN=$(openssl rand -hex 16)      # keep it: Modal won't show it again
uvx modal secret create swarmory-server SWARMORY_OPERATOR_TOKEN=$TOKEN ANTHROPIC_API_KEY=... OPENAI_API_KEY=...
swarmory deploy
swarmory login URL $TOKEN
```

`deploy` prints `URL`. After `login`, every `swarmory` command goes to that server, and runs go on with your laptop
closed. Your machine needs uv, git and swarmory, but no coding agents: they are in the agents' image. Each agent's
sandbox reaches the server and nothing else, unless `--allow` adds a host. A mission's `image.sh`, if it has one,
runs as root with the network open and adds system tools to the agents' image, which is built once and then reused.
The server runs all the time, so it spends Modal compute between runs too.

| To | Run |
|---|---|
| Update swarmory | `uv tool upgrade swarmory`, then `swarmory deploy`. Running agents go on, and the URL stays the same. |
| Change a key | `uvx modal secret create --force swarmory-server ...` with every value, the same token too, then `swarmory deploy`. |
| Stop the server | `swarmory stop RUN` for each run, then `uvx modal app stop swarmory`. Runs stay in the Modal Volume `swarmory-data`, and `swarmory deploy` serves them again. |
| Use a local server again | `mv ~/.swarmory/login ~/.swarmory/login.modal`, and stop any local server from before, `pkill -f '[s]warmory.cli serve'`. The next `start` starts a new one. Move the file back to return to Modal. |

## Use it from an MCP client

```sh
claude mcp add swarmory -- swarmory mcp
```

## Safety

- **Locally, agents run as you with permission prompts off.** On Modal, each agent is sandboxed and never sees your keys.
- **Every run spends.** Set `--budget`.

## Develop

`swarmory/` is the package: the CLI (`cli.py`), the server (`control_plane/`), where agents run (`backends/`), the
coding agents (`harness/`), and the agent and job workers (`worker/`). `skills/` holds the skills that
`npx skills add vtrivedy/swarmory` installs. They ship from GitHub, not with the package. `missions/` holds missions
to copy and run, each with its own skills in `.agents/skills/`.

```sh
uvx --python 3.12 --with-editable . pytest tests -q -p no:cacheprovider
bash tests/e2e.sh    # a real run with real model calls: it spends
```

`tests/model/Delivery.tla` models how messages reach an agent. Check it after a change to `Team.inbox`, `Team.next` or
`Team.mark_read`.

## Credits

Inspired by the open source projects [swarmchat](https://github.com/Pranjal2041/swarmchat) and [openinspect](https://github.com/open-inspect/background-agents).

## License

[MIT](LICENSE)
