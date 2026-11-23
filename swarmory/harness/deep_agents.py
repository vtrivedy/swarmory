"""LangChain's Deep Agents, from its Python SDK: deepagents_session.py builds the agent and runs one session in an
environment uv makes with the SDK. Its model names its provider (anthropic/..., openai/..., gemini/...), and it calls
the gateway as that provider's API."""
from pathlib import Path

from .base import Endpoint, Login, cut, gateway, records, short

SDK = ("deepagents==0.7.30", "langchain-openai==1.8.5", "langchain-google-genai==4.5.2",
       "langgraph-checkpoint-sqlite==3.1.3")  # Anthropic's comes with deepagents
SESSION = str(Path(__file__).with_name("deepagents_session.py"))
CACHE = "/opt/uv-cache"  # in the agents' image, uv's cache, with the SDK already in it


class DeepAgents:
    """It takes no args from a team: deepagents_session.py reads only its model, its prompt and --resume."""
    name = "deepagents"
    cli = "uv"
    install = ("pip install uv", f"UV_CACHE_DIR={CACHE} uv run -q --no-project " +
               " ".join(f"--with {p}" for p in SDK) + " python -c 'import deepagents'")
    provider = None
    login = None
    login_from = ""
    home = ".deepagents"
    conversations = "conversations"
    hooks = True  # deepagents_session.py runs `swarmory inbox --hook` before each model call
    takes_args = False
    # a Modal sandbox can't reach PyPI, where uv revalidates a stale cache: the image's cache has the SDK
    sandboxed = {"UV_CACHE_DIR": CACHE, "UV_OFFLINE": "1"}

    def env(self, llm: Endpoint) -> dict:
        return gateway(llm)

    def prepare(self, work: str, llm: Endpoint, model: str | None = None, meta: dict | None = None) -> None:
        pass

    def inbox(self, text: str) -> dict:
        return {"text": text}

    def command(self, prompt: str, resume: bool, model: str | None, args: list) -> list:
        return ["uv", "run", "-q", "--no-project", *(w for p in SDK for w in ("--with", p)), "python", SESSION,
                model or "", prompt, *(["--resume"] if resume else [])]

    def signin(self, login: str) -> Login | None:
        return None

    def parse(self, lines):
        """deepagents_session.py prints events in swarmory's shape, so this only shortens each text and input."""
        for e in records(lines):
            yield {k: cut(v) if k == "text" else short(v) if k == "input" else v for k, v in e.items()}
