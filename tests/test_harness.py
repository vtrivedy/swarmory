"""A coding agent swarmory does not build in joins a team from its commands."""
import importlib
import json
import shutil
import sys
import types

import pytest

from swarmory import harness
from swarmory.control_plane.runs import new_run
from swarmory.harness.base import INBOX, Endpoint
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
                     SWARMORY_SETUP="echo bad >&2; exit 3", SWARMORY_DIR=str(tmp_path),
                     SWARMORY_URL="http://x", SWARMORY_TOKEN="t", SWARMORY_MODEL="anthropic/claude-haiku-5-5",
                     SWARMORY_ARGS="").items():
        monkeypatch.setenv(k, v)
    (tmp_path / "work").mkdir()
    (tmp_path / "prompt.md").write_text("hi\n")
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


def test_team_sessions_read_no_agent_docs_from_the_repo(tmp_path, monkeypatch):
    """The brief is the mission's instructions: Claude Code reads only its user settings, which hold the inbox hook,
    Codex reads no AGENTS.md, in a fresh session or a resumed one, and pi reads no context files."""
    monkeypatch.setenv("HOME", str(tmp_path))
    llm = Endpoint("http://server/llm", "tok")
    claude, codex, pi = (harness.named(n) for n in ("claude-code", "codex", "pi"))
    for resume in (False, True):
        cmd = claude.command("go", resume, None, [])
        assert cmd[cmd.index("--setting-sources") + 1] == "user"
        assert "--no-context-files" in pi.command("go", resume, "anthropic/claude-haiku-5-5", [])
    claude.prepare(str(tmp_path / "work"), llm)
    assert json.loads((tmp_path / ".claude" / "settings.json").read_text()) == {"hooks": INBOX}
    codex.prepare(str(tmp_path / "work"), llm)
    config = (tmp_path / ".codex" / "config.toml").read_text()
    assert "project_doc_max_bytes = 0\n" in config.split("[")[0], "not a top-level key"


def test_the_missions_skills_are_linked_where_every_coding_agent_finds_them(tmp_path, monkeypatch):
    home, work = tmp_path / "home", tmp_path / "work"
    monkeypatch.setenv("HOME", str(home))
    for name in ("a", "b"):
        (work / ".agents" / "skills" / name).mkdir(parents=True)
        (work / ".agents" / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    (work / ".agents" / "skills" / "notes").mkdir()  # no SKILL.md: not a skill
    (home / ".claude" / "skills").mkdir(parents=True)
    (home / ".claude" / "skills" / "own").symlink_to(tmp_path)  # not the mission's: left alone
    agent.link_skills(work)
    agent.link_skills(work)
    for d in agent.SKILLS:
        assert {p.name for p in (home / d).iterdir()} - {"own"} == {"a", "b"}
        assert (home / d / "a" / "SKILL.md").is_file()
    shutil.rmtree(work / ".agents" / "skills" / "b")
    agent.link_skills(work)
    assert sorted(p.name for p in (home / ".claude" / "skills").iterdir()) == ["a", "own"]
    assert [p.name for p in (home / ".agents" / "skills").iterdir()] == ["a"]
