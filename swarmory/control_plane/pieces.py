"""Pieces: the parts of a run's behavior a team may swap. A piece is one Python module of plainly named functions. Each
name is of one of three kinds, and the kind says how two pieces that define it combine:

    CHOICES  one owner. The default piece owns it, unless one listed piece defines it; two listed pieces is an error.
    VOTES    every piece is asked, and one refusal refuses.
    EVERY    every piece is called, defaults first, then the listed ones in their order.

A choice or a vote about a member is asked only about agents: for the operator and the server itself the default
piece answers, so a piece never has to say what the operator may do."""
import hashlib
import importlib
import types
from dataclasses import dataclass

CHOICES = ("route", "start_at", "refs", "asleep", "model", "render")
VOTES = ("may_call", "may_spend")
EVERY = ("apply", "tick", "prompt", "news", "me", "left")
DEFAULTS = ("mail", "turns")  # the prototype's; the design has board, git, staffing, models and prompt too


@dataclass(frozen=True)
class Route:
    to: list               # whose inbox the message goes in now
    audience: list = None  # who may ever read it; None: whoever may read its conversation
    held: list = ()        # whom the piece left out of `to` for now: logged for the piece's own apply()


def load(name: str, source: str):
    """A listed piece, from the source the run's config holds: never from a path, and never from the run's repo."""
    module = types.ModuleType(f"swarmory_piece_{name}")
    module.NAME = name
    exec(compile(source, f"<piece {name}>", "exec"), module.__dict__)
    return module


class Pieces:
    def __init__(self, listed=(), drop=(), cfg=None):
        """`listed`: [{name, source}] as config.json holds them; `drop`: default pieces left out; `cfg`: name -> table."""
        self.cfg = cfg or {}
        self.defaults = [importlib.import_module(f"swarmory.pieces.{n}") for n in DEFAULTS if n not in drop]
        for m in self.defaults:
            m.NAME = m.__name__.rpartition(".")[2]
        self.listed = [load(p["name"], p["source"]) for p in listed]
        self.all = self.defaults + self.listed
        self.sha = {p["name"]: hashlib.sha256(p["source"].encode()).hexdigest()[:12] for p in listed}
        self.owner, self.default = {}, {}
        for name in CHOICES:
            base = [m for m in self.defaults if hasattr(m, name)]
            mine = [m for m in self.listed if hasattr(m, name)]
            if len(mine) > 1:
                raise ValueError(f"{mine[0].NAME} and {mine[1].NAME} both define {name}, which has one owner: put "
                                 "them in one piece")
            if base or mine:
                self.default[name] = base[0] if base else mine[0]
                self.owner[name] = mine[0] if mine else base[0]

    def choose(self, name: str, agent: bool, *args):
        """The owner's answer, or the default piece's when the choice is not about an agent."""
        m = (self.owner if agent else self.default)[name]
        return getattr(m, name)(*args)

    def owned(self, name: str) -> str:
        return self.owner[name].NAME

    def refused(self, name: str, *args):
        """(the piece, its reason) of the first piece that refuses, or None."""
        for m in self.all:
            if hasattr(m, name) and (why := getattr(m, name)(*args)):
                return m.NAME, why
        return None

    def every(self, name: str, *args) -> list:
        return [getattr(m, name)(*args) for m in self.all if hasattr(m, name)]

    def table(self) -> list:
        """What `swarmory start --dry-run` prints: who owns each choice, and who votes and listens."""
        rows = [f"{n:9} {self.owner[n].NAME}" + ("" if self.owner[n] is self.default[n] else
                                                   f"  (in place of {self.default[n].NAME})") for n in self.owner]
        return rows + [f"{n:9} {', '.join(m.NAME for m in self.all if hasattr(m, n))}" for n in VOTES + EVERY
                       if any(hasattr(m, n) for m in self.all)]
