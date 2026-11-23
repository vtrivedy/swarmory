"""Any other coding agent that runs headless from a command line, from the `run`, `resume` and optional `setup`
commands of its team.toml entry. README.md's paragraph on a coding agent swarmory doesn't build in has the full
contract. The session and `setup` get the gateway as each of base.APIS, with the agent's run token, which the gateway
takes in place of a key. Run locally, the agent's command must already be on the server's PATH, as `setup` does not
install it there; its output is kept as text, and messages reach it at its next turn only."""
import os
import re
import shlex
import subprocess

from .base import Endpoint, Login, cut, gateway

PLACEHOLDER = re.compile(r"\{(?:prompt|model|provider|model_id)\}")


class Command:
    hooks = False
    takes_args = True
    provider = None
    login = None
    login_from = ""
    home = ".swarmory"
    conversations = None
    sandboxed = {}
    install = ()  # not in the agents' image: in a sandbox `setup` can install it; locally it is on the server's PATH

    def __init__(self, name: str, run: str = "", resume: str = "", setup: str = ""):
        self.name, self.run, self.resume, self.setup = name, run, resume, setup
        self.cli = (shlex.split(run) or [""])[0]

    def env(self, llm: Endpoint) -> dict:
        return gateway(llm)

    def prepare(self, work: str, llm: Endpoint, model: str | None = None, meta: dict | None = None) -> None:
        if self.setup:
            r = subprocess.run(["sh", "-c", self.setup], cwd=work, env={**os.environ, **self.env(llm)},
                               capture_output=True, text=True)
            if r.returncode:
                detail = (r.stderr or r.stdout).strip()
                raise RuntimeError(f"setup failed (exit {r.returncode})" + (f": {detail}" if detail else ""))

    def command(self, prompt: str, resume: bool, model: str | None, args: list) -> list:
        words = shlex.split(self.resume if resume else self.run)
        raw = model or ""
        provider, slash, rest = raw.partition("/")
        subs = {"{prompt}": prompt, "{model}": raw, "{provider}": provider if slash else "",
                "{model_id}": rest if slash else raw}
        return [PLACEHOLDER.sub(lambda m: subs[m[0]], w) for w in words] + args

    def signin(self, login: str) -> Login | None:
        return None

    @staticmethod
    def parse(lines):
        for line in lines:
            if line.strip():
                yield {"kind": "say", "text": cut(line.rstrip("\n"))}
