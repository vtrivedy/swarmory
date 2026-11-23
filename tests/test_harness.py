"""A coding agent swarmory does not build in joins a team from its commands."""
import pytest

from swarmory import harness
from swarmory.control_plane.runs import new_run
from swarmory.harness.base import Endpoint

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


def test_a_command_harness_fills_its_commands_and_points_setup_at_the_gateway(tmp_path):
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


def test_a_team_entry_needs_both_commands_and_a_model_that_names_its_provider():
    for bad in ({**AIDER, "resume": ""}, {**AIDER, "run": "aider -m"}, {**AIDER, "model": "haiku"},
                {"harness": "claude-code", "run": "x {prompt}"}, {"harness": "pi", "model": "haiku"},
                {"harness": "pi", "model": "xai/grok-4.3"}):
        with pytest.raises(ValueError):
            start(bad)
