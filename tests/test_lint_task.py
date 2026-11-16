"""swarmory_harbor/skills/eval-factory/scripts/lint_task.py: a sealed task passes, and each isolation or grading break
fails."""
import json

from lint_task import main

TASK = {
    "task.toml": """schema_version = "1.4"
artifacts = ["/app/output"]
[environment]
network_mode = "no-network"
[agent]
user = "agent"
[verifier]
environment_mode = "separate"
""",
    "environment/Dockerfile": "FROM python:3.12-slim\nRUN useradd agent\nUSER agent\n",
    "tests/Dockerfile": "FROM python:3.12-slim\nCOPY verifier.py test.sh /tests/\n",
    "tests/test.sh": "#!/bin/bash\nset -euo pipefail\nrm -f /logs/verifier/reward.*\n"
                     "python3 -I /tests/verifier.py /app/output /logs/verifier\n",
    "tests/verifier.py": "import json, pathlib, sys\n"
                         "pathlib.Path(sys.argv[2], 'reward.json').write_text(json.dumps({'reward': 1.0}))\n",
    "tests/cases/reference/output/answer.txt": "42\n",
}

BREAKS = {
    "verifier_separate": ("task.toml", 'environment_mode = "separate"', 'environment_mode = "shared"'),
    "agent_user": ("task.toml", 'user = "agent"', 'user = "root"'),
    "dockerfile_user": ("environment/Dockerfile", "USER agent", "USER root"),
    "solver_network_narrow": ("task.toml", 'network_mode = "no-network"', 'network_mode = "public"'),
    "image_has_no_task_files": ("environment/Dockerfile", "USER agent", "COPY . /app\nUSER agent"),
    "test_sh_wipes_reward": ("tests/test.sh", "rm -f /logs/verifier/reward.*\n", ""),
    "reference_reward_numeric": ("tests/verifier.py", "{'reward': 1.0}", "{'reward': 1.0, 'gate': 'none'}"),
}


def write_task(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def failed(capsys, task):
    code = main([str(task)])
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return code, {row["check"] for row in rows if row["status"] == "fail"}


def test_a_sealed_task_passes_and_each_break_fails_only_its_check(tmp_path, capsys):
    assert failed(capsys, write_task(tmp_path / "sealed", TASK)) == (0, set())
    for check, (name, old, new) in BREAKS.items():
        files = dict(TASK, **{name: TASK[name].replace(old, new)})
        assert failed(capsys, write_task(tmp_path / check, files)) == (1, {check})
    files = {k: v for k, v in TASK.items() if k != "tests/Dockerfile"}
    assert failed(capsys, write_task(tmp_path / "no-verifier-image", files)) == (1, {"verifier_dockerfile"})
