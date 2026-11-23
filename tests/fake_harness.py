"""A third harness, for tests only: what adding a coding agent to swarmory takes. `Fake` is the one file a new harness
is, and a test adds it to HARNESSES, the one list. CLI is its coding agent, a script that records how it was started,
keeps a conversation and says one thing."""
from swarmory.harness.base import Endpoint, Login, records

CLI = """#!{python}
import json, pathlib, sys
home = pathlib.Path.home()
with open(home / "calls.jsonl", "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:]}}) + "\\n")
(home / ".fake" / "talks").mkdir(parents=True, exist_ok=True)
(home / ".fake" / "talks" / "one.jsonl").write_text("{{}}\\n")
print(json.dumps({{"said": "working"}}), flush=True)
"""


class Fake:
    name = "fake"
    cli = "fake-agent"
    install = ()
    provider = "openai"
    login = "FAKE_LOGIN"
    login_from = "a login no one has"
    home = ".fake"
    conversations = "talks"
    sandboxed = {}
    hooks = False
    takes_args = True

    def env(self, llm: Endpoint) -> dict:
        return {}

    def prepare(self, work: str, llm: Endpoint, model: str | None = None, meta: dict | None = None) -> None:
        pass

    def command(self, prompt: str, resume: bool, model: str | None, args: list) -> list:
        return [self.cli, "resume" if resume else "new", prompt, *args]

    def signin(self, login: str) -> Login | None:
        return None

    def parse(self, lines):
        for e in records(lines):
            yield {"kind": "say", "text": e.get("said")}
