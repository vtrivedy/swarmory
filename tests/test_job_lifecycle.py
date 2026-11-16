import json

import pytest

from swarmory import harbor
from swarmory.control_plane.supervisor import Supervisor
from test_jobs import git, task


@pytest.fixture
def server_jobs(serve, tmp_path, monkeypatch):
    monkeypatch.setattr(Supervisor, "loop", lambda self: None)
    folder = task(tmp_path)
    mission = folder.parents[1]
    (mission / "BRIEF.md").write_text("Run the committed task.\n")
    git(mission, "add", "BRIEF.md")
    git(mission, "commit", "-qm", "Add mission")
    served = serve()
    runs = [served.start(mission=mission, jobs=True) for _ in range(2)]

    def submit(run, agent="nop", attempts=25, **params):
        commit = served.server.runs.get(run.id).head()
        reply = run(served.token, "job", commit=commit, path="tasks/hello",
                    params={"agent": agent, "attempts": attempts, **params})
        assert "job" in reply, reply
        return reply["job"]["id"]

    return served, runs, submit


def test_every_queued_job_of_every_run_starts_on_the_next_tick(server_jobs):
    served, runs, submit = server_jobs
    for run in runs:
        submit(run, "claude-code", model="m")
        for _ in range(3):
            submit(run)
    served.server.supervisor.tick()
    jobs = [j for run in runs for j in run(served.token, "jobs")["jobs"]]
    assert len(jobs) == 8 and all(j["status"] == "running" for j in jobs)
    assert len([s for s in served.backend.specs.values() if s.role == "job"]) == 8


def test_cross_owner_cancel_is_refused_without_changing_the_job(server_jobs):
    served, runs, submit = server_jobs
    jid = submit(runs[0])
    served.server.supervisor.tick()
    member = runs[0].workers("ada")["ada"]
    refused = runs[0](member, "cancel", id=jid)
    assert refused["status"] == 403
    assert runs[0](served.token, "job-show", id=jid)["job"]["status"] == "running"


@pytest.mark.parametrize("errors", [1, 25])
def test_result_message_reports_every_error_after_200_trials(server_jobs, tmp_path, errors):
    served, runs, submit = server_jobs
    jid = submit(runs[0])
    served.server.supervisor.tick()
    results = tmp_path / "results"
    for i in range(200 + errors):
        folder = results / f"trial-{i:03}"
        folder.mkdir(parents=True)
        result = ({"verifier_result": {"rewards": {"reward": int(i == 0)}}} if i < 200 else
                  {"exception_info": {"exception_type": "EnvironmentStartTimeoutError",
                                      "exception_message": f"first line {i}\nsecond line"}})
        (folder / "result.json").write_text(json.dumps(result))
    report = harbor.report(results, 0, tmp_path / "unused.log")
    worker = served.backend.specs[f"{runs[0].id}/job/{jid}"].env["SWARMORY_TOKEN"]
    reply = runs[0](worker, "finish", id=jid, **report)
    assert reply["job"]["outcome"]["headline"] == f"1 passed, 199 failed, {errors} errored of {200 + errors}"
    messages = runs[0](served.token, "read", all=True)["messages"]
    text = next(m["text"] for m in messages if f"job {jid} finished" in m["text"])
    for i in range(200, 200 + errors):
        assert f"trial-{i:03}: EnvironmentStartTimeoutError: first line {i}" in text
    assert "second line" not in text


@pytest.fixture
def modal_provider(monkeypatch):
    import sys
    from types import SimpleNamespace

    sandboxes = []

    class Sandbox:
        def __init__(self, tag, session):
            self.tags = {"swarmory.job": tag, "harbor.session_id": session}
            self.ended = False
            sandboxes.append(self)

        def poll(self):
            return 0 if self.ended else None

        def terminate(self):
            self.ended = True

        @staticmethod
        def list(app_id, tags=None):
            assert app_id == "harbor-app"
            return [sb for sb in sandboxes if all(sb.tags.get(k) == v for k, v in (tags or {}).items())]

    def lookup(name):
        assert name == "__harbor__"
        return SimpleNamespace(app_id="harbor-app")

    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        App=SimpleNamespace(lookup=lookup), Sandbox=Sandbox,
        exception=SimpleNamespace(NotFoundError=LookupError)))
    return Sandbox


def test_a_lost_workers_job_fails_once_and_its_sandboxes_end(server_jobs, modal_provider, monkeypatch):
    from swarmory.control_plane import supervisor as supervision

    served, runs, submit = server_jobs
    monkeypatch.setattr(supervision, "GONE", 0)
    jid = submit(runs[0], env="modal")
    supervisor = served.server.supervisor
    supervisor.tick()
    tag = runs[0](served.token, "job-show", id=jid)["job"]["tag"]
    trial, verifier = modal_provider(tag, "hello__one__env"), modal_provider(tag, "hello__one__verifier__test")
    other = modal_provider(tag + "-other", "hello__one__env")
    served.backend.up.remove(f"{runs[0].id}/job/{jid}")
    supervisor.tick()
    supervisor.tick()
    assert runs[0](served.token, "job-show", id=jid)["job"]["status"] == "failed"
    assert trial.ended and verifier.ended and not other.ended
    messages = runs[0](served.token, "read", all=True)["messages"]
    assert sum(f"job {jid} failed" in m["text"] for m in messages) == 1
