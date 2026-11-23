"""An agent's turns: when the next one starts, what it is told, and how each message for it arrives once, between its
tool calls or at its next turn. Real runs prove the rest."""
import json
import threading

import pytest
from test_team import make

from swarmory import cli
from swarmory.control_plane import team as team_
from swarmory.harness.antigravity import Antigravity
from swarmory.harness.claude_code import ClaudeCode
from swarmory.harness.codex import Codex
from swarmory.harness.pi import Pi
from swarmory.worker import agent


def held(t, who):
    """t.next(who) in a thread, so a test can post while it holds; join() gives its answer."""
    out = {}
    th = threading.Thread(target=lambda: out.update(t.next(who, {"timeout": 5})))
    th.start()
    return th, out


def test_the_next_turn_starts_at_once_unless_the_agent_waits_and_each_message_reaches_it_once(tmp_path, monkeypatch):
    monkeypatch.setattr(team_, "SETTLE", 0.2)
    t = make(tmp_path / "run")
    t.send("grace", {"to": "general", "text": "plan: grace takes i1"})
    turn = t.next("grace", {"timeout": 0})
    assert turn["done"] and turn["messages"] == []  # at once, with nothing new
    assert t.wait("grace", {})["done"] is False  # nothing for her: she waits
    assert t.next("grace", {"timeout": 0}) == {"done": False}
    th, out = held(t, "grace")
    t.send("ada", {"to": "general", "text": "fyi, nothing for anyone"})  # for no one: she stays held
    t.send("ada", {"to": "grace", "text": "can you review 3f2a1c?"})
    th.join()
    assert out["done"] and [m["text"] for m in out["messages"]] == ["can you review 3f2a1c?"]
    assert out["channels"] == {"general": 1}  # ada's fyi, counted; grace's own plan is not unread
    assert t.next("grace", {"timeout": 0})["done"]  # that turn ended her wait

    monkeypatch.setattr(team_, "IDLE", 0.3)
    t.wait("grace", {})
    assert t.next("grace", {"timeout": 1})["done"]  # no message came: the turn starts anyway, after IDLE

    t.send("ada", {"to": "grace", "text": "and i2 too"})
    assert [m["text"] for m in t.inbox("grace", {})["messages"]] == ["and i2 too"]  # between tool calls
    assert t.inbox("grace", {})["messages"] == []
    turn = t.next("grace", {"timeout": 0})
    assert turn["done"] and turn["messages"] == []  # the hook showed it: the turn does not show it again
    assert t.me("grace", {})["unread"] == {"general": 1}  # what was shown is read; the unshown fyi is not

    # tests/model/Delivery.tla's path for ReadMeansSeen: a post, then a mention in the same channel, then the hook
    t.send("alan", {"to": "general", "text": "also fyi"})
    t.send("alan", {"to": "general", "text": "@grace look at this"})
    assert [m["text"] for m in t.inbox("grace", {})["messages"]] == ["@grace look at this"]
    assert t.me("grace", {})["unread"] == {"general": 3}  # both fyis stay unread: the mention did not hide them


class Turns:
    """The worker's `swarmory wait`, `swarmory next` and `swarmory spend --json`, recording each wait and next."""

    def __init__(self, spend):
        self.spend, self.asked = spend, []

    def __call__(self, cmd, **popen):
        if cmd[:2] == ["swarmory", "spend"]:
            return 0, json.dumps(self.spend)
        assert cmd[:2] in (["swarmory", "wait"], ["swarmory", "next"]), cmd
        self.asked.append(cmd[1])
        return 0, "Since your last turn:\nMessages for you:\n  vivek: budget raised\n"


def test_a_spent_budget_holds_the_agent_until_a_message_and_keeps_its_conversation(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "DEADLINE", 2e9)
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: None)
    for k in ("SWARMORY_URL", "SWARMORY_TOKEN", "SWARMORY_NAME", "SWARMORY_ID"):
        monkeypatch.setitem(agent.ENV, k, "x")
    a = object.__new__(agent.Agent)
    a.sessions, a.run = tmp_path / "sessions", Turns({"budget": 1, "by": {"ada": {"usd": 1.2}}})
    a.clone, a.saver, a.started, a.count = lambda: None, lambda: None, lambda: False, lambda: 0
    a.set_aside = lambda *_: pytest.fail("a spent budget threw away the conversation")
    prompts = []

    def session(prompt, fresh):
        prompts.append(prompt)
        if len(prompts) == 3:
            agent.DEADLINE = 0
        return 1 if len(prompts) == 1 else 0  # the gateway refuses the first turn's calls

    a.session = session
    a.live()
    assert a.run.asked == ["wait", "next", "next"] and prompts[1].startswith("Since your last turn:")


def test_a_command_agent_resumes_only_after_a_session_that_succeeded(monkeypatch, tmp_path):
    for k, v in dict(SWARMORY_HARNESS="aider", SWARMORY_RUN="aider -m {prompt}", SWARMORY_SETUP="",
                     SWARMORY_RESUME="aider --restore-chat-history -m {prompt}", SWARMORY_DIR=str(tmp_path),
                     SWARMORY_PROMPT="the mission", SWARMORY_URL="http://x", SWARMORY_TOKEN="t", SWARMORY_MODEL="",
                     SWARMORY_ARGS="").items():
        monkeypatch.setenv(k, v)
    a = agent.Agent()
    a.sessions.mkdir()
    a.upload = lambda sync: None
    exits, cmds = [127, 0, 0, 0], []  # its first session fails: say `aider` is not installed yet

    def run(cmd, **_):
        cmds.append(cmd)
        return exits[len(cmds) - 1], ""

    a.run = run
    for i in range(4):
        a.session("carry on", a.set_aside(2, 1) if i == 3 else "")
    assert [c[:2] for c in cmds] == [["aider", "-m"], ["aider", "-m"], ["aider", "--restore-chat-history"],
                                     ["aider", "-m"]]
    assert [c[-1].split("\n")[0] for c in cmds] == ["the mission", "the mission", "carry on", "the mission"]


def test_every_harness_with_a_hook_gets_messages_mid_turn_in_its_own_answer(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    ClaudeCode().prepare(str(tmp_path / "work"), None)
    Codex().prepare(str(tmp_path / "work"), type("Endpoint", (), {"url": "http://x"})())
    Antigravity().prepare(str(tmp_path / "work"), None)
    monkeypatch.setenv("SWARMORY_MODEL", "anthropic/claude-haiku-5-5")
    Pi().prepare(str(tmp_path / "work"), type("Endpoint", (), {"url": "http://x"})())
    for f, event in ((".claude/settings.json", "PostToolUse"), (".codex/hooks.json", "PostToolUse"),
                     (".gemini/config/hooks.json", "PreInvocation")):
        hooks = json.loads((tmp_path / f).read_text())
        hook = (hooks["hooks"][event][0]["hooks"] if "hooks" in hooks else hooks["swarmory"][event])[0]
        assert hook["command"] == "swarmory inbox --hook", f
    assert '"swarmory", ["inbox", "--hook"]' in (tmp_path / ".pi/agent/extensions/swarmory.ts").read_text()
    assert "--dangerously-bypass-hook-trust" in Codex().command("go", True, None, [])  # Codex runs it unreviewed
    monkeypatch.setattr(cli, "LOGIN", str(tmp_path / ".swarmory" / "login"))
    (tmp_path / ".swarmory").mkdir()
    asked = []
    monkeypatch.setattr(cli, "post", lambda verb, body, timeout: asked.append(verb) or {"text": "[swarmory] ada: hi"})
    monkeypatch.setenv("SWARMORY_HARNESS", "claude-code")
    cli.inbox_hook()
    said = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert said == {"hookEventName": "PostToolUse", "additionalContext": "[swarmory] ada: hi"}
    cli.inbox_hook()  # the next tool call, within INBOX_EVERY: no request
    assert asked == ["inbox"] and capsys.readouterr().out == ""
    (tmp_path / ".swarmory" / "inbox-asked").unlink()
    monkeypatch.setenv("SWARMORY_HARNESS", "antigravity")
    cli.inbox_hook()
    assert json.loads(capsys.readouterr().out) == {"injectSteps": [{"systemMessage": {"systemMessage":
                                                                                      "[swarmory] ada: hi"}}]}
    (tmp_path / ".swarmory" / "inbox-asked").unlink()
    monkeypatch.setenv("SWARMORY_HARNESS", "pi")
    cli.inbox_hook()
    assert json.loads(capsys.readouterr().out) == {"text": "[swarmory] ada: hi"}
