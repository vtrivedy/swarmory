"""The team model, and a run on the one server, where a bug would be silent: whom a token lets in and which DMs it
shows, the push log, the seed, and replaying the log after a crash."""
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest
from conftest import code

from swarmory import seed
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


def git_in(work: Path, *args, email="ada@swarmory.local"):
    return subprocess.run(["git", "-C", str(work), "-c", "user.name=Ada L", "-c", f"user.email={email}", *args],
                          check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}).stdout


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


def test_each_push_is_logged_accepted_or_refused_and_main_cannot_be_rewritten_or_deleted(control_plane, tmp_path):
    work = tmp_path / "w"
    remote = control_plane.url.replace("://", f"://ada:{control_plane.tokens['ada']}@") + "/git/work.git"
    git_in(tmp_path, "clone", "-q", remote, str(work))
    seeded = git_in(work, "rev-parse", "HEAD").strip()
    git_in(work, "commit", "-q", "--allow-empty", "-m", "start")
    git_in(work, "branch", "side")
    first = git_in(work, "rev-parse", "HEAD").strip()
    git_in(work, "push", "-q", remote, "main", "side")
    git_in(work, "commit", "-q", "--amend", "--allow-empty", "-m", "start over")
    rewrite = git_in(work, "rev-parse", "HEAD").strip()
    with pytest.raises(subprocess.CalledProcessError):
        git_in(work, "push", "-q", "--force", remote, "main")
    with pytest.raises(subprocess.CalledProcessError):
        git_in(work, "push", "-q", remote, ":main")
    git_in(work, "clone", "-q", remote, str(tmp_path / "c"))
    assert git_in(work, "ls-remote", remote, "main").split()[0] == first

    zero = "0" * 40
    pushes = [{k: v for k, v in ev.items() if k not in ("seq", "at")}
              for ev in map(json.loads, (control_plane.data / "log.jsonl").read_text().splitlines())
              if ev["type"] == "push"]
    assert pushes == [
        {"type": "push", "by": "vivek", "ref": "refs/heads/main", "old": zero, "new": seeded, "ok": True},
        {"type": "push", "by": "ada", "ref": "refs/heads/main", "old": seeded, "new": first, "ok": True},
        {"type": "push", "by": "ada", "ref": "refs/heads/side", "old": zero, "new": first, "ok": True},
        {"type": "push", "by": "ada", "ref": "refs/heads/main", "old": first, "new": rewrite, "ok": False,
         "reason": "non-fast-forward"},
        {"type": "push", "by": "ada", "ref": "refs/heads/main", "old": first, "new": zero, "ok": False,
         "reason": "deletion prohibited"}]


def test_the_seed_is_the_mission_less_the_grader_its_secrets_and_what_its_gitignore_ignores(serve, tmp_path):
    served = serve()
    run = served.start(seed=False)
    mission = tmp_path / "m"
    (mission / "lib").mkdir(parents=True)
    for rel in ("BRIEF.md", "score.py", "lib/score.py", ".env", "lib/a.pem", "build.log", "lib/x.py"):
        (mission / rel).write_text("x")
    (mission / ".gitignore").write_text("*.log\n")
    (tmp_path / "outside").write_text("x")
    (mission / "link").symlink_to(tmp_path / "outside")
    (mission / "skill").symlink_to(seed.HERE / "skills" / "swarmory")  # swarmory's own skills may be linked
    seed.make_seed(mission, run.url, served.token)
    work = tmp_path / "w"
    remote = run.url.replace("://", f"://vivek:{served.token}@") + "/git/work.git"
    subprocess.run(["git", "clone", "-q", remote, str(work)], check=True, capture_output=True)
    assert sorted(git_in(work, "ls-files").split()) == [".gitignore", "BRIEF.md", "lib/score.py", "lib/x.py",
                                                        "skill/SKILL.md"]


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
    t.send("vivek", {"to": "dm:ada:vivek", "text": "status?", "nonce": "s8"})
    t.read("ada", {"limit": 2})
    assert t.sleep("grace", {"timeout": 0})["woke"], "grace's @mention should have woken her"


def views(t: Team) -> list:
    """What each member sees, each woken by what would wake them."""
    return [(t.me(x, {}), t.issues(x, {}), t.issues(x, {"status": "done"}), t.issue(x, {"id": "i1"}),
             t.issue(x, {"id": "i2"}), t.read(x, {"all": True, "limit": 1000}), t.sleep(x, {"timeout": 0}))
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
    (tmp_path / "bad").mkdir()
    shutil.copy(t.dir / "config.json", tmp_path / "bad")
    (tmp_path / "bad" / "log.jsonl").write_bytes(good.replace(b"\n", b"\nnot json\n", 1))
    with pytest.raises(ValueError):
        Team(tmp_path / "bad")
