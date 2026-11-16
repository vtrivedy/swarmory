"""Prints `trace <job> <trial> reward <reward> steps <steps>` for each trial in a Harbor job's artifacts, the .tar.gz
that `swarmory get` saves: its reward from result.json and the number of steps in its agent's trajectory.json.

    python3 tools/trajectory.py PATH.tar.gz
"""
import json
import sys
import tarfile


def summary(path: str) -> list:
    out = {}
    with tarfile.open(path) as tar:
        for m in tar.getmembers():
            parts = m.name.split("/")
            if not m.isfile() or len(parts) < 4:  # job-<id>/<id>/<trial>/...: beside Harbor's folder are the logs
                continue
            job, trial = parts[1], parts[2]
            row = out.setdefault(trial, {"job": job, "trial": trial, "reward": None, "steps": None})
            if parts[3:] == ["result.json"]:
                r = json.load(tar.extractfile(m))
                row["reward"] = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
            elif parts[3:] == ["agent", "trajectory.json"]:
                row["steps"] = len(json.load(tar.extractfile(m)).get("steps") or [])
    return [f"trace {r['job']} {r['trial']} reward {r['reward']} steps {r['steps']}" for r in out.values()]


if __name__ == "__main__":
    print("\n".join(summary(sys.argv[1])))
