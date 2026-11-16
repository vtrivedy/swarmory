"""The periodic wake: a sleeping agent no one wakes is woken wake_every seconds after its last session ends, to find
work; a real wake still comes at once; and `swarmory send` warns that a message of files alone wakes no one. The
worker's clock and its `swarmory sleep` are fakes here, so an hour of a run takes no time."""
import json

import pytest
from test_team import make

from swarmory import WAKE_EVERY, cli, seed
from swarmory.control_plane.supervisor import Supervisor
from swarmory.worker import agent

HOUR = 3600


class Clock:
    """The worker's time module: sleep() moves the clock on."""

    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Sleeper:
    """`swarmory sleep --timeout S` as the worker runs it: woken at `at` with `text`, else timed out after S. Until
    `down` the server does not answer, and it exits 1 after S, as the CLI does. `swarmory spend --json` answers
    `spend`."""

    def __init__(self, clock, at=None, text="", down=None, spend=None):
        self.clock, self.at, self.text, self.down, self.spend, self.asked = clock, at, text, down, spend, []

    def __call__(self, cmd, **popen):
        if cmd[:2] == ["swarmory", "spend"]:
            return 0, json.dumps(self.spend or {"budget": None, "by": {}})
        assert cmd[:3] == ["swarmory", "sleep", "--timeout"], cmd
        timeout = int(cmd[3])
        self.asked.append(timeout)
        if self.down is not None and self.clock.now < self.down:
            self.clock.now += timeout
            return 1, ""
        if self.at is not None and self.at <= self.clock.now + timeout:
            self.clock.now = max(self.clock.now, self.at)
            self.at = None
            return 0, self.text + "\n"
        self.clock.now += timeout
        return 4, ""


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(agent, "time", c)
    return c


def sleeper(monkeypatch, clock, every, deadline_in, **wake):
    """An Agent whose only part is wake(), sleeping in a Sleeper."""
    monkeypatch.setattr(agent, "DEADLINE", int(clock.now + deadline_in))
    a = object.__new__(agent.Agent)
    a.every, a.run = every, Sleeper(clock, **wake)
    return a


def test_no_wake_for_the_interval_wakes_the_agent_with_the_periodic_prompt(monkeypatch, clock):
    a, start = sleeper(monkeypatch, clock, WAKE_EVERY, HOUR), clock.now
    said = a.wake()
    assert clock.now - start == WAKE_EVERY == 240 and max(a.run.asked) <= WAKE_EVERY  # under the 5 min prompt cache
    assert "56 min left" in said and "`swarmory`" in said and "`git log`" in said
    assert "review, break or debate" in said and "never with @all" in said and "End your turn only after" in said
    assert "`swarmory job list --mine`" in said


def test_while_the_server_is_down_no_periodic_wake_starts_a_session_that_could_only_fail(monkeypatch, clock):
    start = clock.now
    a = sleeper(monkeypatch, clock, WAKE_EVERY, HOUR, down=clock.now + 600)
    said = a.wake()
    assert said.startswith(agent.IDLE) and clock.now >= start + 600 + agent.RETRY  # after a sleep that got through


def test_a_real_wake_comes_at_once_with_its_own_text(monkeypatch, clock):
    a = sleeper(monkeypatch, clock, WAKE_EVERY, HOUR, at=clock.now + 42, text="ada (DM): can you review i4?")
    start = clock.now
    assert a.wake() == "ada (DM): can you review i4?" and clock.now - start == 42


def test_a_wake_every_of_0_sleeps_until_a_real_wake_or_the_deadline(monkeypatch, clock):
    a = sleeper(monkeypatch, clock, 0, HOUR)
    assert a.wake() == "" and clock.now == agent.DEADLINE


def test_no_periodic_wake_in_the_last_minute_before_the_deadline(monkeypatch, clock):
    a = sleeper(monkeypatch, clock, WAKE_EVERY, WAKE_EVERY + agent.LATE - 1)  # the wake would leave 59 s
    assert a.wake() == "" and clock.now == agent.DEADLINE
    a = sleeper(monkeypatch, clock, WAKE_EVERY, WAKE_EVERY + agent.LATE)
    assert a.wake().startswith("No one has woken you")


def living(monkeypatch, clock, tmp_path, session, **sleep):
    """An Agent that runs live() for an hour, its sessions `session`, sleeping in a Sleeper."""
    monkeypatch.setattr(agent, "DEADLINE", int(clock.now + HOUR))
    for k in ("SWARMORY_URL", "SWARMORY_TOKEN", "SWARMORY_NAME", "SWARMORY_ID"):
        monkeypatch.setitem(agent.ENV, k, "x")
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: None)  # login and git config
    a = object.__new__(agent.Agent)
    a.every, a.sessions, a.run = WAKE_EVERY, tmp_path / "sessions", Sleeper(clock, **sleep)
    a.session, a.clone, a.saver, a.started, a.count = session, lambda: None, lambda: None, lambda: False, lambda: 0
    return a


def test_an_agent_that_ends_each_session_at_once_gets_one_session_per_interval(monkeypatch, clock, tmp_path):
    """The timer starts when a session ends, and wake() runs only between sessions: periodic wakes never stack, never
    cut a session short, and cost at most one session per interval, however fast the agent ends its turn."""
    starts, prompts = [], []

    def session(prompt, fresh):
        starts.append(clock.now)
        prompts.append(prompt)
        clock.now += 5  # the agent looks, and ends its turn
        return 0

    living(monkeypatch, clock, tmp_path, session).live()
    gaps = [y - x for x, y in zip(starts, starts[1:])]
    assert gaps and all(g == WAKE_EVERY + 5 for g in gaps), gaps
    assert len(starts) == 15  # 0, 245, ... 3430: the next would be due after the deadline
    assert all(p.startswith(agent.IDLE) for p in prompts[1:])


def test_once_the_budget_is_spent_a_failed_session_sleeps_until_a_message_and_keeps_its_conversation(
        monkeypatch, clock, tmp_path):
    starts, prompts = [], []

    def session(prompt, fresh):
        starts.append(clock.now)
        prompts.append(prompt)
        clock.now += 5
        return 1  # the gateway refuses every call

    a = living(monkeypatch, clock, tmp_path, session, at=clock.now + 1000, text="vivek (DM): budget raised",
               spend={"budget": 1, "by": {"ada": {"usd": 1.2}}})
    a.set_aside = lambda *_: pytest.fail("a spent budget threw away the conversation")
    begun = clock.now
    a.live()
    assert [s - begun for s in starts] == [0, 1000] and prompts[1] == "vivek (DM): budget raised"


def test_a_message_of_files_alone_wakes_no_one_in_a_channel_and_send_says_so(tmp_path, monkeypatch, capsys):
    team = make(tmp_path / "run")

    def place(fid):
        (tmp_path / "run" / "files").mkdir(exist_ok=True)
        (tmp_path / "run" / "files" / fid).write_bytes(b"@grace @all please take i4\n")

    note = team.upload("vivek", "note.txt", 28, place)["file"]["id"]
    sent = team.send("vivek", {"to": "general", "text": "", "files": [note]})["message"]
    assert sent["mentions"] == [] and team.wake[sent["id"]] == []  # the server never reads a file for names

    small, big, binary = tmp_path / "note.txt", tmp_path / "big.txt", tmp_path / "bin"
    small.write_text("@grace @alan please take i4\n@grace again\n")
    big.write_text("@grace " + "x" * (16 << 10))
    binary.write_bytes(b"@grace \xff\xfe")
    said = cli.unheard("", [str(small)], "general")
    assert "wakes no one" in said and "@grace, @alan" in said and "names in the text" in said
    assert "only the issue's followers" in cli.unheard("", [str(small)], "i4")
    for f in (big, binary):
        assert "@grace" not in cli.unheard("", [str(f)], "general")
    assert cli.unheard("", [str(small)], "dm:ada:vivek") is None  # a DM wakes its other member regardless
    assert cli.unheard("see the note", [str(small)], "general") is None

    monkeypatch.setattr(cli, "upload", lambda path, url, token: "f1")
    monkeypatch.setattr(cli, "load_login", lambda: ("http://127.0.0.1:1", "t"))
    for to, conv in (("general", "general"), ("ada", "dm:ada:vivek")):
        monkeypatch.setattr(cli, "call", lambda cmd, body, retry=True, conv=conv: {
            "data": {"message": {"id": "m1", "conv": conv}}, "text": f"Sent m1 to {conv}."})
        with pytest.raises(SystemExit):
            cli.command(["send", to, "--file", str(small)])
        err = capsys.readouterr().err
        assert ("swarmory: warning: this message has no text, so it wakes no one" in err) == (to == "general"), err


def test_wake_every_goes_from_team_toml_or_the_flag_to_the_run_and_its_workers(serve, tmp_path, monkeypatch):
    mission = tmp_path / "m"
    mission.mkdir()
    (mission / "BRIEF.md").write_text("# Nothing\n")
    (mission / "team.toml").write_text('wake_every = 600\n\n[[agent]]\nharness = "codex"\ncount = 2\n')
    assert cli.read_team(mission / "team.toml") == ([{"harness": "codex"}] * 2, 600)
    asked = []
    monkeypatch.setattr(cli, "saved", lambda: {"url": "http://127.0.0.1:1", "token": "t"})
    monkeypatch.setattr(cli, "answers", lambda url: True)
    monkeypatch.setattr(cli, "fetch", lambda url, path, token, req: asked.append(req) or {"data": {"run": "r"}})
    monkeypatch.setattr(cli, "save_login", lambda *a: None)
    monkeypatch.setattr(seed, "make_seed", lambda *a: None)
    cli.start_run(mission, wake_every=None)
    cli.start_run(mission, wake_every=0)
    (mission / "team.toml").write_text('[[agent]]\nharness = "codex"\n')
    cli.start_run(mission, wake_every=None)
    assert [r.get("wake_every") for r in asked] == [600, 0, None]
    (mission / "team.toml").write_text("wake_every = 600\n")  # a team from flags, a cadence from the mission
    assert cli.read_team(mission / "team.toml") == (None, 600)
    cli.start_run(mission, team=[{"harness": "codex"}], wake_every=None)
    assert asked[-1]["team"] == [{"harness": "codex"}] and asked[-1]["wake_every"] == 600
    with pytest.raises(SystemExit, match="no \\[\\[agent\\]\\] entries"):
        cli.start_run(mission, wake_every=None)

    given = []
    monkeypatch.setattr(cli, "start_run", lambda *a, **k: given.append(k) or {"data": {}, "text": ""})
    for argv in (["m", "--wake-every", "0"], ["m", "--wake-every", "300"], ["m"]):
        cli.start(argv)
    assert [k["wake_every"] for k in given] == [0, 300, None] and type(given[1]["wake_every"]) is int
    with pytest.raises(SystemExit):
        cli.start(["m", "--wake-every", "soon"])

    served = serve()
    for every, want in ((None, "240"), (0, "0"), (900.0, "900")):
        run = served.start(**({} if every is None else {"wake_every": every}))
        run.workers("ada")
        spec = served.backend.specs[f"{run.id}/agent/ada"]
        assert spec.env["SWARMORY_WAKE_EVERY"] == want
        assert served("status", run=run.id)["wake_every"] == int(want)
    for bad in (5, -1, "300", True):
        assert "wake_every" in served("start", mission="m", team=[{"harness": "codex"}], wake_every=bad)["error"]


def test_a_run_from_before_wake_every_gets_no_periodic_wake():
    class Run:
        id = "old"
        config = {"deadline": 2e9, "operator": "vivek", "members": [
            {"id": "ada", "name": "Ada", "harness": "codex", "kind": "agent"}]}

    spec = Supervisor(None, None, "http://127.0.0.1:1").agent(Run, Run.config["members"][0], "t")
    assert spec.env["SWARMORY_WAKE_EVERY"] == "0"
