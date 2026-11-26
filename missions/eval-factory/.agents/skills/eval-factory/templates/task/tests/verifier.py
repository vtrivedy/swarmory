"""Verify the solver's /app/output against key.json.

Writes the full record to grade.json and only numeric values to reward.json, because Harbor rejects non-numeric
entries in reward.json.
"""
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys

VERSION = "1"
KEY = Path(__file__).with_name("key.json")
MAX_BYTES = 1 << 20


def record(reward, failed_check, checks, partial=0.0):
    return {
        "reward": reward,
        "partial_score": partial,
        "verifier_version": VERSION,
        "failed_check": failed_check,
        "checks": checks,
    }


def parse_number(text):
    try:
        value = Decimal(text.replace(",", "").strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def grade(output_dir, key, hardening=True):
    """Return the grade record for one submission. `hardening=False` proves each exploit case was real."""
    answer = Path(output_dir) / "answer.txt"
    if not answer.is_file() or answer.is_symlink():
        return record(0.0, "answer_file", [{"id": "answer_file", "passed": False}])
    # Read raw bytes so invalid UTF-8 fails a check instead of crashing the verifier.
    with answer.open("rb") as file:
        data = file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return record(0.0, "answer_size", [{"id": "answer_size", "passed": False}])
    text = data.decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if hardening and len(lines) != 1:
        return record(0.0, "one_answer", [{"id": "one_answer", "passed": False, "value": len(lines)}])
    expected = parse_number(str(key["answer"]))
    parsed = [parse_number(line) for line in lines]
    answer_ok = expected is not None and any(value == expected for value in parsed if value is not None)
    checks = [
        {"id": "answer", "passed": answer_ok},  # blocks wrong numeric values and garbage bytes
        {
            "id": "nothing_else_written",
            "passed": sorted(p.name for p in Path(output_dir).iterdir()) == ["answer.txt"],
        },  # blocks collateral files in /app/output
    ]
    required = [c for c in checks if not c["id"].startswith(("diag_", "decoy_"))]  # diagnostics never score
    passed = [c["passed"] for c in required]
    failed = next((c["id"] for c in required if not c["passed"]), None)
    return record(float(all(passed)), failed, checks, sum(passed) / len(passed))


def write(result, verifier_dir):
    verifier_dir = Path(verifier_dir)
    (verifier_dir / "grade.json").write_text(json.dumps(result, indent=1))
    (verifier_dir / "reward.json").write_text(
        json.dumps({"reward": result["reward"], "partial_score": result["partial_score"]})
    )


if __name__ == "__main__":
    write(grade(sys.argv[1], json.loads(KEY.read_text())), sys.argv[2])
