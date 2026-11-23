"""The coding agents a team can be made of. HARNESSES is the one list of built-in ones: a new built-in harness is one
file in this folder and one entry here. base.py says what a file holds. Any other coding agent joins a team as a
Command (command.py), from the commands its team.toml entry gives."""
from .antigravity import Antigravity
from .base import Harness
from .claude_code import ClaudeCode
from .codex import Codex
from .command import Command
from .deep_agents import DeepAgents
from .pi import Pi

HARNESSES: list[Harness] = [ClaudeCode(), Codex(), Antigravity(), DeepAgents(), Pi()]
COMMAND_FIELDS = ("run", "resume", "setup")


def named(name: str) -> Harness:
    """The built-in harness a team calls `name`. Raises KeyError for any other name."""
    for h in HARNESSES:
        if h.name == name:
            return h
    raise KeyError(name)


def of(member: dict) -> Harness:
    """The harness of a team entry or a run's member: a built-in one by name, else a Command from its commands."""
    try:
        return named(member["harness"])
    except KeyError:
        return Command(member["harness"], member.get("run") or "", member.get("resume") or "",
                       member.get("setup") or "")
