"""skills/eval-factory: crash accounting, refusals, decoys and first attempts in summarize_runs."""
import json

from summarize_runs import summarize


def trial(job, name, reward=None, exception=None, started="2026-11-03T00:00:00"):
    folder = job / name
    folder.mkdir(parents=True)
    result = {"trial_name": name, "task_name": "team/task", "started_at": started,
              "config": {"agent": {"model_name": "lab/model"}},
              "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
              "exception_info": {"exception_type": exception} if exception else None}
    (folder / "result.json").write_text(json.dumps(result))


def test_only_the_clock_and_the_grade_count_against_the_model_and_decoys_and_first_attempts_count(tmp_path):
    trial(tmp_path, "solved", 1.0)
    trial(tmp_path, "wrong", 0.0)
    trial(tmp_path, "out-of-time", 0.0, "AgentTimeoutError")
    trial(tmp_path, "too-big", 0.0, "NonZeroAgentExitCodeError")
    trial(tmp_path, "out-of-tokens", 0.0, "OutputTokenExceededError")
    trial(tmp_path, "never-graded", None, "RuntimeError")
    trial(tmp_path, "stalled", 0.0, "AgentTimeoutError")
    trial(tmp_path, "out-of-context", 0.0, "ContextWindowExceededError")
    trial(tmp_path, "refused", None, "AgentSafetyRefusalError")
    [row] = summarize([tmp_path], forced=["stalled"])
    status = {t["trial"]: t["status"] for t in row["trials"]}
    assert status == {"solved": "pass", "wrong": "fail", "out-of-time": "fail", "too-big": "infrastructure_failure",
                      "out-of-tokens": "infrastructure_failure", "never-graded": "infrastructure_failure",
                      "stalled": "infrastructure_failure", "out-of-context": "fail", "refused": "refusal"}
    assert (row["passes"], row["scored"], row["refusals"], row["infrastructure_failures"]) == (1, 5, 1, 4)

    decoys = tmp_path / "decoys"
    trial(decoys, "fooled", 0.0)
    trial(decoys, "careful", 1.0)
    (decoys / "fooled/verifier").mkdir()
    checks = [{"id": "decoy_not_taken", "passed": False}]
    (decoys / "fooled/verifier/grade.json").write_text(json.dumps({"checks": checks}))
    [row] = summarize([decoys])
    assert row["decoys_taken"] == 1

    first = tmp_path / "first"
    trial(first, "late-pass", 1.0, started="2026-11-03T02:00:00")
    trial(first, "early-fail", 0.0, started="2026-11-03T01:00:00")
    [row] = summarize([first], first=1)
    assert (row["passes"], row["scored"]) == (0, 1)

