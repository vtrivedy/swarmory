"""The team model, and a run on the one server, where a bug would be silent: whom a token lets in and which DMs it
shows, the push log, the seed, replaying the log after a crash, and a message whose names are only in a file."""
import functools
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from conftest import code

from swarmory import cli, seed
from swarmory.control_plane.policy import sha
from swarmory.control_plane.team import Team

IDS = ["ada", "grace", "alan", "edsger", "barbara", "donald", "ken", "dennis", "margaret", "frances", "vivek"]


def make(tmp: Path) -> Team:
    tmp.mkdir(exist_ok=True)
    members = [{"id": x, "name": x.title(), "kind": "agent", "token_sha256": sha(f"tok-{x}")} for x in IDS[:-1]]
    members.append({"id": IDS[-1], "name": IDS[-1].title(), "kind": "human", "operator": True})
    (tmp / "config.json").write_text(json.dumps({"run": "t-1", "mission": "t", "deadline": 2e9, "members": members,
                                                 "channels": [{"id": "general", "about": "all"}],
                                                 "view_sha256": sha("tok-view")}))
    return Team(tmp)


@pytest.fixture
def control_plane(serve):
    """A run of ten agents on the one server, seeded, so the server has started each agent's worker (Stub's) and
    minted its token; vivek is the operator. `api(who, verb)` is a verb as a member, `code(path, token)` a raw
    request's status."""
    served = serve()
    run = served.start(team=[{"harness": "claude-code"} for _ in IDS[:-1]])
    tokens = {**run.workers(*IDS[:-1]), "vivek": served.token, "view": run.view}

    def api(who, verb, **req):
        out = run(tokens.get(who, who), verb, **req)
        assert "status" not in out, out
        return out

    api.url, api.tokens = run.url, tokens
    api.code = lambda path, who, body=b"{}", basic=False: code(run.url + path, tokens.get(who, who), body, basic)
    api.data = served.server.data / "runs" / run.id
    yield api


def git_in(work: Path, *args, email="ada@swarmory.local", config=os.devnull):
    return subprocess.run(["git", "-C", str(work), "-c", "user.name=Ada L", "-c", f"user.email={email}", *args],
                          check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1",
                               "GIT_TERMINAL_PROMPT": "0"}).stdout


def test_a_token_opens_only_its_doors_the_view_token_only_reads_and_a_dm_stays_between_its_two(control_plane):
    r = urllib.request.Request(f"{control_plane.url}/feed?log=0&ledger=0",
                               headers={"Authorization": f"Bearer {control_plane.tokens['view']}"})
    log = json.loads(urllib.request.urlopen(r, timeout=30).read())["log"]
    assert [e["member"]["id"] for e in log if e["type"] == "member"] == IDS
    assert not any("sha256" in json.dumps(e) for e in log), "the viewer got a token's digest"
    feeds = [control_plane.code("/feed", who) for who in ("", "tok-nobody", "alan")]
    assert feeds == [401, 401, 403], "no token, an unknown one, and a member's, which the table gives no view"
    assert control_plane.code("/api/me", "view") == 403  # the view token reads, and only that
    assert control_plane.code("/view", "") == 200
    assert control_plane.code("/api/me", "tok-nobody") == 401
    assert control_plane.code("/llm/anthropic/v1/messages", "tok-nobody") == 401
    refs = "/git/work.git/info/refs?service=git-upload-pack"
    assert control_plane.code(refs, control_plane.tokens["grace"] + "!", None, basic=True) == 401
    assert control_plane.code(refs, "ada", None, basic=True) == 200
    control_plane("ada", "send", to="grace", text="the key is under the mat")
    assert control_plane.code("/api/read", "alan", b'{"conv": "dm:ada:grace", "all": true}') == 404
    assert [m["text"] for m in control_plane("vivek", "read", conv="dm:ada:grace", all=True)["messages"]] == [
        "the key is under the mat"], "the operator can read every DM"
    assert control_plane.code("/api/send", "vivek", b'{"to": "dm:ada:grace", "text": "hi"}') == 404
    assert [m["text"] for m in control_plane("grace", "read")["messages"]] == ["the key is under the mat"]


def test_each_push_is_logged_accepted_or_refused_and_main_cannot_be_rewritten_or_deleted(control_plane, tmp_path,
                                                                                         monkeypatch):
    """ada logs in as an agent does, and clones and pushes `control-plane:work.git` with the token from `swarmory
    git-credential`, which no remote URL, git config or config file shows."""
    token, swarmory = control_plane.tokens["ada"], tmp_path / "bin" / "swarmory"
    swarmory.parent.mkdir()
    swarmory.write_text(f'#!/bin/sh\nexec {sys.executable} -m swarmory.cli "$@"\n')
    swarmory.chmod(0o755)
    login, config = tmp_path / "sw" / "login", tmp_path / "global"
    config.write_text(f"[include]\n\tpath = {login.parent / 'gitconfig'}\n")
    monkeypatch.setenv("PATH", f"{swarmory.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("SWARMORY_LOGIN", str(login))
    subprocess.run([swarmory, "login", control_plane.url, token], check=True, capture_output=True)
    git = functools.partial(git_in, config=config)
    work, remote = tmp_path / "w", "control-plane:work.git"
    git(tmp_path, "clone", "-q", remote, str(work))
    assert git(work, "remote", "get-url", "origin").strip() == control_plane.url + "/git/work.git"
    shown = git(work, "config", "-l")
    assert "helper=!swarmory git-credential" in shown
    assert token not in shown + (login.parent / "gitconfig").read_text()
    seeded = git(work, "rev-parse", "HEAD").strip()
    git(work, "commit", "-q", "--allow-empty", "-m", "start")
    git(work, "branch", "side")
    first = git(work, "rev-parse", "HEAD").strip()
    git(work, "push", "-q", remote, "main")
    with pytest.raises(subprocess.CalledProcessError):  # work on a branch is work nobody sees
        git(work, "push", "-q", remote, "side")
    git(work, "commit", "-q", "--amend", "--allow-empty", "-m", "start over")
    rewrite = git(work, "rev-parse", "HEAD").strip()
    with pytest.raises(subprocess.CalledProcessError):
        git(work, "push", "-q", "--force", remote, "main")
    with pytest.raises(subprocess.CalledProcessError):
        git(work, "push", "-q", remote, ":main")
    git(work, "clone", "-q", remote, str(tmp_path / "c"))
    assert git(work, "ls-remote", remote, "main").split()[0] == first

    zero = "0" * 40
    pushes = [{k: v for k, v in ev.items() if k not in ("seq", "at")}
              for ev in map(json.loads, (control_plane.data / "log.jsonl").read_text().splitlines())
              if ev["type"] == "push"]
    assert pushes == [
        {"type": "push", "by": "vivek", "ref": "refs/heads/main", "old": zero, "new": seeded, "ok": True},
        {"type": "push", "by": "ada", "ref": "refs/heads/main", "old": seeded, "new": first, "ok": True},
        {"type": "push", "by": "ada", "ref": "refs/heads/side", "old": zero, "new": first, "ok": False,
         "reason": "pre-receive hook declined"},
        {"type": "push", "by": "ada", "ref": "refs/heads/main", "old": first, "new": rewrite, "ok": False,
         "reason": "non-fast-forward"},
        {"type": "push", "by": "ada", "ref": "refs/heads/main", "old": first, "new": zero, "ok": False,
         "reason": "deletion prohibited"}]


def test_the_seed_is_the_mission_less_its_secrets_and_what_its_gitignore_ignores(serve, tmp_path):
    served = serve()
    run = served.start(seed=False)
    mission = tmp_path / "m"
    (mission / "lib").mkdir(parents=True)
    for rel in ("BRIEF.md", "check.sh", ".env", "lib/a.pem", "build.log", "lib/x.py"):
        (mission / rel).write_text("x")
    (mission / ".gitignore").write_text("*.log\n")
    (tmp_path / "outside").write_text("x")
    (mission / "link").symlink_to(tmp_path / "outside")
    (mission / ".agents" / "skills" / "s").mkdir(parents=True)  # as `npx skills add` installs a skill in a mission
    (mission / ".agents" / "skills" / "s" / "SKILL.md").write_text("x")
    (mission / ".claude" / "skills").mkdir(parents=True)
    (mission / ".claude" / "skills" / "s").symlink_to("../../.agents/skills/s")
    seed.make_seed(mission, run.url, served.token)
    work = tmp_path / "w"
    remote = run.url.replace("://", f"://vivek:{served.token}@") + "/git/work.git"
    subprocess.run(["git", "clone", "-q", remote, str(work)], check=True, capture_output=True)
    skill = [".agents/skills/s/SKILL.md", ".claude/skills/s/SKILL.md"]
    assert sorted(git_in(work, "ls-files").split()) == sorted([".gitignore", "BRIEF.md", "check.sh", "lib/x.py", *skill])


def busy(t: Team):
    t.channel("ada", {"name": "results", "about": "numbers", "nonce": "c1"})
    t.join("grace", {"name": "#results"})
    t.leave("alan", {"name": "general"})
    m = t.send("ada", {"to": "results", "text": "@grace @all 3x faster", "nonce": "s1"})["message"]
    t.send("grace", {"to": "results", "text": "nice", "reply_to": m["id"], "nonce": "s2"})
    t.new("ada", {"title": "Speed", "body": "ask @Edsger", "assignees": ["grace"], "nonce": "s3"})
    t.send("ken", {"to": "i1", "text": "on it", "nonce": "s4"})
    t.move("grace", {"id": "i1", "status": "done", "note": "pushed in speed.py", "nonce": "s5"})
    t.new("ken", {"title": "Docs", "assignees": ["alan", "barbara"], "nonce": "s6"})
    t.assign("ken", {"id": "i2", "remove": ["alan"], "nonce": "s7"})
    t.join("dennis", {"name": "i2"})
    t.leave("ken", {"name": "i2"})
    t.new("ada", {"title": "Solo", "nonce": "s9"})
    assert t.assign("ada", {"id": "i3", "add": ["me"], "if_unassigned": True})["issue"]["assignees"] == ["ada"]
    with pytest.raises(ValueError, match="already assigned to ada"):
        t.assign("grace", {"id": "i3", "add": ["me"], "if_unassigned": True})
    assert t.assign("grace", {"id": "i3", "add": ["me"]})["issue"]["assignees"] == ["ada", "grace"]
    assert cli.request("assign", ["i3", "+me"], {"--if-unassigned": True})["if_unassigned"] is True
    t.send("vivek", {"to": "dm:ada:vivek", "text": "status?", "nonce": "s8"})
    t.read("ada", {"limit": 2})
    assert t.wait("grace", {"timeout": 0})["done"], "grace's @mention should have woken her"


def views(t: Team) -> list:
    """What each member sees, each woken by what would wake them."""
    return [(t.me(x, {}), t.issues(x, {}), t.issues(x, {"status": "done"}), t.issue(x, {"id": "i1"}),
             t.issue(x, {"id": "i2"}), t.read(x, {"all": True, "limit": 1000}), t.next(x, {"timeout": 0}))
            for x in IDS]


def restarted(data: Path, into: Path) -> Team:
    """A server restarted on a copy of DATA."""
    shutil.copytree(data, into)
    return Team(into)


def test_the_log_replays_to_what_everyone_saw_and_a_torn_last_line_is_dropped(tmp_path):
    t = make(tmp_path / "live")
    busy(t)
    good = t.path.read_bytes()
    replayed = restarted(t.dir, tmp_path / "replayed")
    shutil.copytree(t.dir, tmp_path / "torn")
    with open(tmp_path / "torn" / "log.jsonl", "ab") as f:
        f.write(b'{"seq": 999, "at": 1.0, "type": "mess')
    torn = Team(tmp_path / "torn")
    assert torn.path.read_bytes() == good
    seen = views(t)
    assert views(replayed) == seen and views(torn) == seen
    torn.send("ada", {"to": "general", "text": "after the tear"})
    assert views(restarted(torn.dir, tmp_path / "again")) == views(torn)
    # Pre-turns logs used "wakes" and "woke" instead of "recipients" and "delivered"
    legacy = tmp_path / "legacy"
    shutil.copytree(t.dir, legacy)
    (legacy / "log.jsonl").write_bytes(good.replace(b'"recipients":', b'"wakes":')
                                       .replace(b'"type": "delivered"', b'"type": "woke"'))
    assert views(Team(legacy)) == seen
    (tmp_path / "bad").mkdir()
    shutil.copy(t.dir / "config.json", tmp_path / "bad")
    (tmp_path / "bad" / "log.jsonl").write_bytes(good.replace(b"\n", b"\nnot json\n", 1))
    with pytest.raises(ValueError):
        Team(tmp_path / "bad")


def test_a_message_of_files_alone_wakes_no_one_in_a_channel_and_send_says_so(tmp_path, monkeypatch, capsys):
    team = make(tmp_path / "run")

    def place(fid):
        (tmp_path / "run" / "files").mkdir(exist_ok=True)
        (tmp_path / "run" / "files" / fid).write_bytes(b"@grace @all please take i4\n")

    note = team.upload("vivek", "note.txt", 28, place)["file"]["id"]
    sent = team.send("vivek", {"to": "general", "text": "", "files": [note]})["message"]
    assert sent["mentions"] == [] and team.recipients[sent["id"]] == []  # the server never reads a file for names

    small, big, binary = tmp_path / "note.txt", tmp_path / "big.txt", tmp_path / "bin"
    small.write_text("@grace @alan please take i4\n@grace again\n")
    big.write_text("@grace " + "x" * (16 << 10))
    binary.write_bytes(b"@grace \xff\xfe")
    said = cli.unheard("", [str(small)], "general")
    assert "reaches no one" in said and "@grace, @alan" in said and "names in the text" in said
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
        assert ("swarmory: warning: this message has no text, so it reaches no one" in err) == (to == "general"), err
