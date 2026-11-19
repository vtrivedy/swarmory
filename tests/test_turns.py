"""An agent's turns: when the next one starts, what it is told, and how each message for it arrives once, between its
tool calls or at its next turn. Real runs prove the rest."""
import json
import threading

import pytest
from test_team import make

from swarmory import cli
from swarmory.control_plane import team as team_
from swarmory.harness.claude_code import ClaudeCode
from swarmory.harness.codex import Codex
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


def test_both_harnesses_get_messages_between_tool_calls_through_one_hook(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    ClaudeCode().prepare(str(tmp_path / "work"), None)
    Codex().prepare(str(tmp_path / "work"), type("Endpoint", (), {"url": "http://x"})())
    for f in (".claude/settings.json", ".codex/hooks.json"):
        hook = json.loads((tmp_path / f).read_text())["hooks"]["PostToolUse"][0]
        assert hook["hooks"][0]["command"] == "swarmory inbox --hook", f
    assert "--dangerously-bypass-hook-trust" in Codex().command("go", True, None, [])  # Codex runs it unreviewed
    monkeypatch.setattr(cli, "LOGIN", str(tmp_path / ".swarmory" / "login"))
    (tmp_path / ".swarmory").mkdir()
    asked = []
    monkeypatch.setattr(cli, "post", lambda verb, body, timeout: asked.append(verb) or {"text": "[swarmory] ada: hi"})
    cli.inbox_hook()
    said = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert said == {"hookEventName": "PostToolUse", "additionalContext": "[swarmory] ada: hi"}
    cli.inbox_hook()  # the next tool call, within INBOX_EVERY: no request
    assert asked == ["inbox"] and capsys.readouterr().out == ""
