"""The verifier is right in both directions: every stored case scores what its expected.json says."""
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from verifier import grade, write  # noqa: E402

HERE = Path(__file__).parent
KEY = json.loads((HERE / "key.json").read_text())
CASES = sorted(p for p in (HERE / "cases").iterdir() if p.is_dir())


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_case_scores_expected(case):
    expected = json.loads((case / "expected.json").read_text())
    result = grade(case / "output", KEY)
    assert result["reward"] == expected["reward"]
    if "failed_check" in expected:
        assert result["failed_check"] == expected["failed_check"]
    if "partial_score" in expected:
        assert result["partial_score"] == expected["partial_score"]
    if "reward_without_hardening" in expected:
        assert grade(case / "output", KEY, hardening=False)["reward"] == expected["reward_without_hardening"]


def test_required_cases_exist():
    names = {case.name for case in CASES}
    assert {"empty", "reference", "alternative", "garbage", "near-miss", "side-effect"} <= names
    assert any(name.startswith("exploit-") for name in names)  # the cheapest shortcut, written as code


def test_instruction_states_every_graded_rule():
    instruction = (HERE.parent / "instruction.md").read_text()
    assert "/app/output/answer.txt" in instruction
    assert "one line" in instruction
    assert "not a link" in instruction  # the verifier rejects a symlinked answer
    assert "Write nothing" in instruction  # the verifier fails any other file in /app/output
    assert "1 MiB" in instruction  # the verifier fails a larger answer


def test_reward_json_is_numbers_only(tmp_path):
    write(grade(HERE / "cases/reference/output", KEY), tmp_path)
    reward = json.loads((tmp_path / "reward.json").read_text())
    assert reward and all(type(v) in (int, float) and math.isfinite(v) for v in reward.values())
    assert json.loads((tmp_path / "grade.json").read_text())["checks"]


def test_grade_is_identical_in_fresh_processes(tmp_path):
    outputs = []
    for run in range(2):
        folder = tmp_path / str(run)
        folder.mkdir()
        subprocess.run([sys.executable, "-I", str(HERE / "verifier.py"), str(HERE / "cases/reference/output"),
                        str(folder)], check=True)
        outputs.append((folder / "grade.json").read_bytes())
    assert outputs[0] == outputs[1]
