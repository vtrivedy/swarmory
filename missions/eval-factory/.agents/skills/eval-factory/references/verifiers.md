# Verifiers and stored cases

Copyable deterministic verifier patterns, error handling, stored cases, and grade records for [SKILL.md](../SKILL.md). For model-based grading and judge calibration, see [references/judges.md](judges.md). Every check returns `{"id": str, "passed": bool}`. Store keys and starting snapshots in `/tests`, which the solver never sees.

Contents: [template interface](#keep-the-template-interface), [error handling](#handle-errors-one-way), [parsed values](#compare-parsed-values), [final state](#check-final-state-and-collateral-scope), [hidden tests](#run-hidden-tests-on-a-clean-copy), [multiple artifacts](#store-cases-for-several-artifacts), [properties and optimizations](#check-properties-and-optimizations), [path diagnostics](#record-the-path-as-a-diagnostic), [stored cases](#stored-cases), and [grade record](#record-the-grade).

## Keep the template interface

[lint_task.py](../scripts/lint_task.py), [summarize_runs.py](../scripts/summarize_runs.py), and `tests/test_cases.py` depend on four parts of [verifier.py](../templates/task/tests/verifier.py).

- Expose `grade(output_dir, key, hardening=True)` returning the [grade record](#record-the-grade).
- Keep the CLI signature `verifier.py <output dir> <verifier dir>`.
- Guard each exploit defense behind `if hardening:`. `test_cases.py` runs each `exploit-<name>` case with `hardening=False` and expects reward `1.0` to prove the hole existed.
- Prefix diagnostic checks with `diag_` and decoy-avoidance checks with `decoy_`. Neither prefix affects `reward` or `partial_score`.

## Handle errors one way

- Treat missing output, non-UTF-8 bytes, malformed syntax, crashes, or timeouts in submitted code as failed checks. Return reward `0.0` with `failed_check` set.
- If the verifier crashes on any solver-written file, fix the bug. Save the crashing input as a stored case.
- Exit nonzero without writing `reward.json` when the verifier cannot reach a verdict. Examples include a judge timeout, an unparseable judge response, a judge refusal, a mock-service outage, or a corrupt file in `/tests`. Harbor records `RewardFileNotFoundError`, which counts as an infrastructure failure to fix and regrade.

## Compare parsed values

Bind every extracted value to its field or label, never bare set membership, so swapped counts or roles cannot pass. Normalize harmless format differences such as commas or trailing zeros. Sort unordered rows or keys unless order is part of the claim. Match boundary operators such as `>=` versus `>`, rounding precision, and wrapper formats verbatim to `instruction.md`.

Never use plain substring matching. Substring checks suffer from negation blindness, where `"not 42"` matches `"42"`, and candidate dumping, where `"41, 42, 43"` matches `"42"`.

```python
import re
from decimal import Decimal

DOLLARS = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?(?![\d.,]*\d)")

def says_amount(reply: str, expected_cents: int) -> dict:
    # Blocks matching order IDs or other numbers as the refund amount.
    amounts = [Decimal(w.replace(",", "") + (c or "")) * 100 for w, c in DOLLARS.findall(reply)]
    return {"id": "says_amount", "passed": amounts == [expected_cents]}
```

## Check final state and collateral scope

Recompute outcomes directly from raw files, database tables, or service ledgers. Never trust a service success flag or environment helper that already decides success. Accept every valid final state rather than brittle syntax proxies, and ensure state complexity prevents random mutations from passing.

When inspecting SQLite or similar databases, open artifacts read-only such as `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)` so grading cannot mutate state. Compare untouched records against snapshots stored in `/tests`.

```python
import json
from pathlib import Path

def check_ledger(ledger_path, starting_entries, charges_path, starting_charges):
    entries = [json.loads(line) for line in Path(ledger_path).read_text().splitlines()]
    charges = json.loads(Path(charges_path).read_text())
    new = entries[len(starting_entries):]
    refunds = [e for e in new if e["type"] == "refund"]
    return [
        {"id": "history_unchanged", "passed": entries[:len(starting_entries)] == starting_entries},
        {"id": "exactly_one_refund", "passed": len(refunds) == 1},
        {"id": "right_refund", "passed": bool(refunds) and all((e["order"], e["cents"]) == (1182, 4210) for e in refunds)},
        {"id": "only_refunds_written", "passed": len(new) == len(refunds)},
        {"id": "charges_unchanged", "passed": charges == starting_charges},  # Blocks deleting the duplicate charge.
    ]
```

## Run hidden tests on a clean copy

Store the clean starting repository in `/tests/start` without `.git`. Add `COPY start/ /tests/start/` and `COPY hidden/ /tests/hidden/` to `tests/Dockerfile`. Copy `/tests/start` into a temporary directory and copy only allowed solver files over it. Run each case as `nobody` in its own process group, and check outputs in the root process.

Copying only `ALLOWED` paths prevents solver tests from colliding with hidden tests. Because `scope` rejects edits outside `ALLOWED`, choose one policy. Declare exact editable files in `instruction.md` and tell solvers to place scratch tests in `/tmp`. Otherwise, exclude untracked scratch tests from `scope` so solvers can test their fix.

Never run `git` on the solver repository. A solver can set `core.fsmonitor` to execute arbitrary code during `git status`, or hide edits with `.git/info/exclude` and `--skip-worktree`.

```python
import filecmp, json, os, shutil, signal, subprocess, tempfile
from pathlib import Path

HERE = Path(__file__).parent
START = str(HERE / "start")
ALLOWED = {"src/pagination.py"}
SKIP = {".git", "__pycache__", ".pytest_cache"}

def regular(path):
    return os.path.isfile(path) and not os.path.islink(path)

def paths(root):
    found = set()
    for folder, dirs, names in os.walk(root):
        links = {d for d in dirs if os.path.islink(os.path.join(folder, d))}
        dirs[:] = [d for d in dirs if d not in SKIP | links]
        found |= {os.path.relpath(os.path.join(folder, n), root) for n in [*names, *links]}
    return found

def passes(work, case):
    try:
        proc = subprocess.Popen(
            ["runuser", "-u", "nobody", "--", "python3", "-I", "src/pagination.py", *case["args"]],
            cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, start_new_session=True,
        )
    except OSError:
        return False
    try:
        out, _ = proc.communicate(case["stdin"], timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return False
    return proc.returncode == 0 and out == case["stdout"]

def check_patch(repo):
    mine, start = paths(repo), paths(START)
    same = {p for p in mine & start if regular(f"{repo}/{p}") and filecmp.cmp(f"{repo}/{p}", f"{START}/{p}", shallow=False)}
    changed_ok = all(regular(f"{repo}/{p}") for p in ALLOWED & mine)
    hidden = json.loads((HERE / "hidden" / "cases.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o755)
        work = os.path.join(tmp, "repo")
        shutil.copytree(START, work)
        for p in ALLOWED:
            target = Path(work, p)
            if p in mine and regular(f"{repo}/{p}"):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f"{repo}/{p}", target)
            elif target.exists():
                target.unlink()
        subprocess.run(["chmod", "-R", "a+rX", work], check=True)
        behavior = all(passes(work, case) for case in hidden)
    return [
        {"id": "behavior", "passed": behavior},
        {"id": "scope", "passed": changed_ok and (mine | start) - same <= ALLOWED},
    ]
```

For a library without a CLI, add a root-owned driver in `/tests` that `nobody` runs to print results.

Verify hidden test and fuzz adequacy before freezing. Vary input types, shapes, and edge cases rather than only random numbers. Confirm every test input changes the output so dead branches cannot pass. Check branch coverage so sparse tests cannot accept broken code.

## Store cases for several artifacts

Keep the CLI signature `verifier.py <output dir> <verifier dir>`. Place extra artifacts such as a service ledger beside `output/` in each case folder, and resolve them with a helper.

```python
from pathlib import Path

def artifact(output_dir, name, container_path):
    stored = Path(output_dir).parent / name
    return stored if stored.exists() else Path(container_path)

ledger = artifact(output_dir, "ledger.jsonl", "/var/lib/payments/ledger.jsonl")
```

List `/var/lib/payments/ledger.jsonl` under `artifacts` in `task.toml`.

## Check properties and optimizations

- **Multiple valid answers.** Check invariant properties instead of matching the reference answer. For a schedule, check that every talk fits its room and no room or speaker overlaps.
- **Optimization tasks.** Score on held-out inputs with a deterministic count such as CPU instructions or rows scanned. Wall-clock times drift across hosts. Normalize between the baseline at `0.0` and the reference at `1.0`. When beating the reference matters, score `1 - cost / baseline` or log uncapped speedup beside the normalized score.

## Record the path as a diagnostic

Record method checks with a `diag_` prefix so you can spot lucky passes without rejecting valid alternative methods.

```python
def path_checks(call_log):
    calls = [json.loads(line) for line in Path(call_log).read_text().splitlines()]
    timeouts = [i for i, c in enumerate(calls) if c["path"] == "/refunds" and c["status"] == 504]
    after = [c["path"] for c in calls[timeouts[0] + 1:]] if timeouts else []
    before_retry = after[:after.index("/refunds")] if "/refunds" in after else after
    return [{"id": "diag_status_before_retry", "passed": "/refunds/status" in before_retry}]
```

## Stored cases

Store each case under `tests/cases/<name>/` with `output/`, any extra service artifacts beside `output/`, and `expected.json`.

| Case | Contents | `expected.json` |
|---|---|---|
| `reference` | Exact output of `solve.sh` in the built image | `{"reward": 1.0}` |
| `alternative` | Valid answer with different incidental format or method | `{"reward": 1.0}` |
| `empty` | Empty `output/` directory | `{"reward": 0.0}` |
| `garbage` | Malformed or corrupted output | `{"reward": 0.0, "failed_check": "<id>"}` |
| `side-effect` | Correct answer plus an unwanted collateral change | `{"reward": 0.0, "failed_check": "<id>"}` |
| `near-miss` | Plausible wrong answer | `{"reward": 0.0, "failed_check": "<id>", "partial_score": <float>}` |
| `exploit-<name>` | Cheapest shortcut written as code, one per shortcut | `{"reward": 0.0, "reward_without_hardening": 1.0}` |

Update [tests/test_cases.py](../templates/task/tests/test_cases.py) so `test_instruction_states_every_graded_rule` asserts one phrase from `instruction.md` per checked rule. Run stored-case checks from the task folder.

```bash
cd tasks/<task-name> && uvx pytest tests -q -p no:cacheprovider
```

When `--container-verifier` is required, run every stored case inside the built verifier image instead of host `pytest`. `verify_task.py free --container-verifier` runs every stored case inside the verifier image automatically, and [run free checks](harbor.md#run-free-checks) shows the manual container loop for Phase 2.

Confirm four properties in your tests.

- Two fresh verifier processes produce identical output.
- The verifier gives identical verdicts when `environment/` is absent.
- Every fact stated in `instruction.md` holds on the key.
- The reference output clears every numeric threshold by a margin.

## Record the grade

Harbor accepts only finite numbers in `/logs/verifier/reward.json`. Write `reward` and `partial_score` to `reward.json`, and write the full check list to `/logs/verifier/grade.json`.

```json
{
  "reward": 0.0,
  "partial_score": 0.75,
  "verifier_version": "2",
  "failed_check": "exactly_one_refund",
  "checks": [
    {"id": "history_unchanged", "passed": true},
    {"id": "exactly_one_refund", "passed": false},
    {"id": "right_refund", "passed": true},
    {"id": "charges_unchanged", "passed": true},
    {"id": "diag_status_before_retry", "passed": false}
  ]
}
```

- Every check without a `diag_` or `decoy_` prefix is required.
- `failed_check` names the first failed required check, or `null` on a pass.
- `partial_score` is the fraction of required checks that passed unless the task defines another formula before the first run.
- Increment `VERSION` in `tests/verifier.py` whenever grading changes, and never mix verifier versions in one table.
