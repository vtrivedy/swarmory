"""The coding agents a team can be made of. HARNESSES is the one list: a new harness is one file in this folder and one
entry here. base.py says what a file holds."""
from .base import Harness
from .claude_code import ClaudeCode
from .codex import Codex

HARNESSES: list[Harness] = [ClaudeCode(), Codex()]


def named(name: str) -> Harness:
    """The harness a team calls `name`. Raises KeyError for any other name."""
    for h in HARNESSES:
        if h.name == name:
            return h
    raise KeyError(name)
