"""What a harness file says about its coding agent. Everything swarmory knows about one coding agent is in its file in
this folder, and HARNESSES (__init__.py) lists them: the server, the backends and worker/agent.py read the list
and name no coding agent themselves. swarmory/harbor.py does name them, as the agents Harbor itself can run in a job's
trials. The package uses only the standard library, as worker/agent.py imports it on an agent's machine.

This file also holds what the parsers share: the cuts that keep a trace's text short."""
import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Protocol

LONG = 6000  # characters of one text field kept; the rest is cut, with a count of what was cut
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")

# After each tool call, messages for the agent that came meanwhile, as context. Claude Code and Codex take the same
# hook and read the same answer, context(); `swarmory inbox --hook` prints the answer of the agent's own harness.
INBOX = {"PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "swarmory inbox --hook",
                                                      "timeout": 15}]}]}


@dataclass(frozen=True)
class Endpoint:
    """Where a session's model calls go: the server's gateway, and the member's run token as the key."""
    url: str  # the gateway, without a provider: http://HOST/llm
    token: str


@dataclass(frozen=True)
class Login:
    """A person's subscription login, read by Harness.signin, as the gateway calls with it."""
    sign: Callable[[dict, str], tuple[dict, str]]  # a call's (headers, path), as the harness sends them when logged in
    expires: float | None = None  # when it stops working, if it says
    upstream: str | None = None  # where a logged-in call goes, when not to the provider's API


class Harness(Protocol):
    """What every harness has. Claude Code and Codex also have once(prompt, model, folder), which tools/report.py runs:
    one session as the person, with their own login, in `folder`, that reads no instruction files from the folders
    above it."""
    name: str  # what a team names it by, in --team and team.toml
    cli: str  # its executable
    install: tuple[str, ...]  # the commands that install it in the agents' image, which has Node
    provider: str | None  # the gateway route it calls, one of APIS; None when its model names it
    login: str | None  # the variable that holds a subscription login; None when it takes none
    login_from: str  # what that variable holds, for the message that asks for it
    home: str  # the folder under HOME it keeps its state in, which lives in the agent's folder and so outlives its
               # machine
    conversations: str | None  # the folder in `home` with its conversations: one there means a session can resume.
                               # None: it resumes after a session that ended with status 0 since its last fresh start
    sandboxed: dict[str, str]  # its environment in a sandbox that is itself the boundary
    hooks: bool  # it runs `swarmory inbox --hook` mid-turn, so messages reach the agent then
    takes_args: bool  # it accepts extra CLI args from team.toml

    def env(self, llm: Endpoint) -> dict[str, str]:
        """The environment of a session whose model calls go to `llm`."""

    def prepare(self, work: str, llm: Endpoint, model: str | None = None, meta: dict | None = None) -> None:
        """Writes the config files a session in the folder `work` needs: the gateway, and the hook that delivers
        messages between tool calls, when the harness has one. Called before each session."""

    def inbox(self, text: str) -> dict:
        """The answer its hook prints to give the agent `text`, the messages that came during its turn."""

    def command(self, prompt: str, resume: bool, model: str | None, args: list[str]) -> list[str]:
        """One headless session that prints its events as lines of JSON: `resume` carries on its last conversation,
        and `args` are a team's own extra arguments."""

    def signin(self, login: str) -> Login | None:
        """The login the variable `login` held, or None if it isn't one."""

    def parse(self, lines: Iterable[str]) -> Iterator[dict]:
        """The events of a session's output, in the shape control_plane/traces.py describes. It yields a line's events
        before it reads the next line, so the caller knows which line each came from."""


APIS = ("anthropic", "openai", "gemini")  # the APIs the gateway serves, and so the providers a model can name


def gateway(llm: Endpoint) -> dict:
    """The gateway as each of APIS, in the variables their SDKs read: for a harness whose model names its
    provider."""
    return {"ANTHROPIC_BASE_URL": f"{llm.url}/anthropic", "ANTHROPIC_API_KEY": llm.token,
            "OPENAI_BASE_URL": f"{llm.url}/openai/v1", "OPENAI_API_KEY": llm.token,
            "GOOGLE_GEMINI_BASE_URL": f"{llm.url}/gemini", "GEMINI_API_KEY": llm.token}


def context(text: str) -> dict:
    """The answer to INBOX that gives the model `text` as context."""
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}


def records(lines: Iterable[str]) -> Iterator[dict]:
    """The lines that are JSON objects."""
    for line in lines:
        try:
            e = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(e, dict):
            yield e


def cut(s, n=LONG) -> str:
    s = ANSI.sub("", s if isinstance(s, str) else json.dumps(s, ensure_ascii=False))
    return s if len(s) <= n else s[:n] + f"\n… {len(s) - n:,} more characters"


def short(x):
    """x with every string in it cut: a tool's arguments, which can hold a whole file."""
    if isinstance(x, str):
        return cut(x)
    if isinstance(x, dict):
        return {k: short(v) for k, v in x.items()}
    return [short(v) for v in x] if isinstance(x, list) else x


def text_of(content) -> str:
    """A tool result's or message's content as text: a string, or a list of blocks (text, images)."""
    if isinstance(content, str):
        return content
    out = []
    for b in content if isinstance(content, list) else ():
        if isinstance(b, dict):
            out.append(b.get("text") if b.get("type") == "text" else f"[{b.get('type', 'block')}]")
    return "\n".join(x for x in out if x)


def other(e: dict) -> dict:
    """An event no parser knows, shortened."""
    return {"kind": "other", "type": str(e.get("type")) + (f"/{e['subtype']}" if e.get("subtype") else ""),
            "text": cut(json.dumps(e, ensure_ascii=False), 600)}
