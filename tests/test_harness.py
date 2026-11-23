"""A coding agent swarmory does not build in joins a team from its commands."""
import importlib
import sys
import types

import pytest

from swarmory import harness
from swarmory.control_plane.runs import new_run
from swarmory.harness.base import Endpoint
from swarmory.worker import agent

AIDER = {"harness": "aider", "model": "anthropic/claude-haiku-5-5", "run": "aider --model {model} -m {prompt}",
         "resume": "aider --restore-chat-history --model {model} -m {prompt}",
         "setup": 'echo "$ANTHROPIC_BASE_URL $ANTHROPIC_API_KEY" > gw'}


class Backend:
    def check(self, harnesses, grants, allow):
        pass


def start(*team):
    owner = lambda m: m.split("/")[0] if m and "/" in m else None  # noqa: E731
    return new_run({"mission": "m", "team": list(team), "me": "vivek"}, Backend(),
                   {"ANTHROPIC_API_KEY": "k", "XAI_API_KEY": "k"}, "token", owner)[0]


def test_a_command_harness_fills_its_commands_and_points_setup_at_the_gateway(monkeypatch, tmp_path):
    config = start(AIDER)
    member = config["members"][0]
    assert {k: member[k] for k in ("run", "resume", "setup")} == {k: AIDER[k] for k in ("run", "resume", "setup")}
    aider = harness.of(member)
    assert aider.command("say {model} hi", False, member["model"], ["-x"]) == \
        ["aider", "--model", "anthropic/claude-haiku-5-5", "-m", "say {model} hi", "-x"]  # a prompt is not filled in
    assert aider.command("go", True, member["model"], [])[:2] == ["aider", "--restore-chat-history"]
    aider.prepare(str(tmp_path), Endpoint("http://server/llm", "tok"))
    assert (tmp_path / "gw").read_text() == "http://server/llm/anthropic tok\n"
    assert not aider.hooks and list(aider.parse(["hello\n", "\n"])) == [{"kind": "say", "text": "hello"}]

    split = harness.of({"harness": "x", "run": "x {provider} {model_id} {prompt}", "resume": "x {prompt}",
                        "setup": "echo bad >&2; exit 3"})
    assert split.command("hi {model_id}", False, "anthropic/claude-haiku-5-5", []) == \
        ["x", "anthropic", "claude-haiku-5-5", "hi {model_id}"]
    with pytest.raises(RuntimeError):
        split.prepare(str(tmp_path), Endpoint("http://server/llm", "tok"))
    for k, v in dict(SWARMORY_HARNESS="x", SWARMORY_RUN="x {prompt}", SWARMORY_RESUME="x {prompt}",
                     SWARMORY_SETUP="echo bad >&2; exit 3", SWARMORY_DIR=str(tmp_path), SWARMORY_PROMPT="hi",
                     SWARMORY_URL="http://x", SWARMORY_TOKEN="t", SWARMORY_MODEL="anthropic/claude-haiku-5-5",
                     SWARMORY_ARGS="").items():
        monkeypatch.setenv(k, v)
    (tmp_path / "work").mkdir()
    a = agent.Agent()
    a.sessions.mkdir()
    a.upload = lambda sync: None
    assert a.session("hi", "") == 1 and "bad" in (a.sessions / "session-001.err").read_text()


def test_a_team_entry_needs_both_commands_and_a_model_that_names_its_provider():
    for bad in ({**AIDER, "resume": ""}, {**AIDER, "run": "aider -m"}, {**AIDER, "model": "haiku"},
                {"harness": "claude-code", "run": "x {prompt}"}, {"harness": "pi", "model": "haiku"},
                {"harness": "pi", "model": "xai/grok-4.3"},
                {"harness": "deepagents", "model": "anthropic/claude-haiku-5-5", "args": ["--x"]}):
        with pytest.raises(ValueError):
            start(bad)


def test_deepagents_marks_resumable_only_after_checkpointing(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://x/anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "tok")
    updates, saved = [], [False]

    class FakeSaver:
        def __init__(self, conn):
            pass

        def get_tuple(self, config):
            return {} if saved[0] else None

    class FakeAgent:
        def stream(self, inp, config, stream_mode="updates"):
            for u in updates:
                if isinstance(u, Exception):
                    raise u
                saved[0] = True
                yield u

    stubs = {"deepagents": {"create_deep_agent": lambda *a, **k: FakeAgent()},
             "deepagents.backends": {"LocalShellBackend": lambda **k: object()},
             "langchain": {}, "langchain.agents": {}, "langchain.agents.middleware": {"before_model": lambda f: f},
             "langchain.chat_models": {"init_chat_model": lambda *a, **k: object()},
             "langchain_core": {}, "langchain_core.messages": {"AIMessage": type("A", (), {}),
                                                               "HumanMessage": lambda t: t, "ToolMessage": type("T", (), {})},
             "langgraph": {}, "langgraph.checkpoint": {}, "langgraph.checkpoint.sqlite": {"SqliteSaver": FakeSaver}}
    for name, attrs in stubs.items():
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, m)
    sys.modules.pop("swarmory.harness.deepagents_session", None)
    ds = importlib.import_module("swarmory.harness.deepagents_session")

    updates[:] = [RuntimeError("503")]
    with pytest.raises(RuntimeError):
        ds.main("anthropic/claude-haiku-5-5", "go")
    assert not ds.CONVERSATIONS.exists()

    updates[:] = [{}]
    ds.main("anthropic/claude-haiku-5-5", "go")
    assert (ds.CONVERSATIONS / "checkpoint").is_file()

    ds.DB.write_bytes(b"stale")
    ds.main("anthropic/claude-haiku-5-5", "fresh")
    assert ds.DB.read_bytes() != b"stale"
