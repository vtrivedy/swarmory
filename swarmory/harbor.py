"""Harbor, which runs every job. A job runs one agent some number of times on a Harbor task folder, or on each task in
a folder of them, in Docker containers on the server's machine or in Modal sandboxes, and reports each trial's
reward. A local server's trials run in Docker by default, where they can reach it, and any other server's on Modal.

Nothing in the task folder chooses the agent, the network or a credential. The parameters choose the agent. A job is
refused, when it is asked for, unless each task.toml is sealed: the environment and the verifier have no network, so
a model agent's trials reach only the server's model gateway, which the Harbor command adds. A `${...}` value
in an env table is refused too, because Harbor fills those from the environment of the process that runs it. The job
worker (swarmory/worker/job.py) runs Harbor with a listed environment: PATH, HOME, the Modal token Harbor starts
sandboxes with, and the job's spend token."""
import copy
import json
import os
import re
import shlex
import tomllib
import urllib.parse
from pathlib import Path

VERSION = "harbor[modal]==0.23.0"
GATEWAY = {"claude-code": ("/anthropic", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"),  # each coding agent's route
           "codex": ("/openai/v1", "OPENAI_BASE_URL", "OPENAI_API_KEY")}             # and the settings it reads
AGENTS = ("oracle", "nop", *GATEWAY)  # Harbor's model-free agents, and the coding agents
CONCURRENT = 8  # the most trials one job runs at once
RETRY = "EnvironmentStartTimeoutError"  # a trial whose sandbox did not start is run once more
APP = "__harbor__"  # the Modal app Harbor starts trial and verifier sandboxes in
TAG = "swarmory.job"  # the Modal tag Harbor puts on each of a job's sandboxes as it starts it: the job's claim's tag
MODAL = ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET")
SEALED = "no-network"
ENVS = ("docker", "modal")
LOOPBACK = ("127.0.0.1", "localhost", "::1")  # a server at one of these is local
FROM_DOCKER = "host.docker.internal"  # the name a Docker container has for the machine that runs it
LOCAL = ("This job's trials would run off this machine and call a model through the server, which is at "
         "127.0.0.1 where they cannot reach it. Run them on this machine (`--env docker`), or use a server deployed "
         "with `swarmory deploy`.")


def task_files(names) -> list:
    """The task.toml files of a job's folder: its own, or one per task folder in it."""
    return sorted(n for n in names if n == "task.toml" or n.count("/") == 1 and n.endswith("/task.toml"))


def templates(doc, where: str = "") -> list:
    """Every `${...}` value in an env table anywhere in a task.toml, as (the table, the key, the value)."""
    found = []
    if isinstance(doc, list):
        for i, item in enumerate(doc):
            found += templates(item, f"{where}[{i}]")
    elif isinstance(doc, dict):
        for key, value in doc.items():
            path = f"{where}.{key}" if where else key
            if key == "env" and isinstance(value, dict):
                found += [(path, k, v) for k, v in value.items() if isinstance(v, str) and "${" in v]
            found += templates(value, path)
    return found


def seal(doc: dict, name: str = "task.toml") -> dict:
    """A task.toml as a job accepts it: no network for the environment or for a verifier's own environment, and no
    phase may widen that. The agent and verifier phases have no network settings of their own, so each takes its
    environment's: a phase that set one, even the same one, would have Harbor switch policy between phases, which some
    environments cannot do. A ValueError names the first `${...}` value in an env table."""
    for table, key, value in templates(doc):
        raise ValueError(f"{name}: [{table}] sets {key} to {value!r}. Harbor fills a ${{...}} value from the "
                         "environment of the job worker, which holds its credentials, so a task may not use one. "
                         "Write the value itself.")
    doc = copy.deepcopy(doc)

    def shut(table: dict):
        table["network_mode"] = SEALED
        for key in ("allowed_hosts", "allow_internet"):
            table.pop(key, None)

    shut(doc.setdefault("environment", {}))
    for holder in (doc, *(s for s in doc.get("steps", ()) if isinstance(s, dict))):
        for phase in (holder.get("agent"), holder.get("verifier")):
            if isinstance(phase, dict):
                for key in ("network_mode", "allowed_hosts", "allow_internet"):
                    phase.pop(key, None)
                if isinstance(phase.get("environment"), dict):
                    shut(phase["environment"])
    return doc


def trials(folder: Path) -> list:
    """Each trial's reward and the first line of its error, from the result.json Harbor writes in each trial's folder."""
    out = []
    for path in sorted(folder.glob("*/result.json")):
        try:
            r = json.loads(path.read_text())
        except ValueError:
            continue
        reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        info = r.get("exception_info") or {}
        said = str(info.get("exception_message", "")).strip().split("\n")[0][:300]
        error = info.get("exception_type") and f"{info['exception_type']}: {said}"
        out.append({"trial": path.parent.name, "reward": reward, "error": error})
    return out


def check(params: dict, inputs, url: str) -> dict:
    """On the server, before the job is queued: the parameters as the job records them, or a ValueError that
    says what is wrong with them or with the folder. `url` is the server's address."""
    extra = params.keys() - {"agent", "model", "attempts", "env"}
    if extra:
        raise ValueError(f"a harbor job takes --agent, --model, --attempts and --env, not {', '.join(sorted(extra))}")
    if params.get("env") not in (None, *ENVS):
        raise ValueError("env is docker, to run trials on the server's machine, or modal")
    agent, model, attempts = params.get("agent"), params.get("model"), params.get("attempts", 1)
    if agent not in AGENTS:
        raise ValueError(f"agent is one of {', '.join(AGENTS)}")
    if (model is None) == (agent in GATEWAY) or model is not None and not (
            isinstance(model, str) and len(model) <= 100 and re.fullmatch(r"[\w.:/@-]+", model)):
        raise ValueError("model is a model name, which claude-code and codex need and oracle and nop do not take")
    if isinstance(attempts, str) and attempts.isdigit():
        attempts = int(attempts)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts is a whole number from 1")
    # with no env, a local server's trials run in Docker, so only trials sent to Modal cannot reach it
    if agent in GATEWAY and params.get("env") == "modal" and urllib.parse.urlsplit(url).hostname in LOOPBACK:
        raise ValueError(LOCAL)
    tasks = task_files(inputs.names())
    if not tasks:
        raise ValueError("the folder is not a Harbor task or a folder of them: it has no task.toml")
    for name in tasks:
        try:
            doc = tomllib.loads(inputs.read(name).decode())
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"{name} is not TOML: {e}") from None
        if seal(doc, name) != doc:
            raise ValueError(f'{name} is not sealed. A job\'s trials get no network but the model gateway: set '
                             f'[environment] network_mode = "{SEALED}", and take network_mode, allowed_hosts '
                             "and allow_internet out of every other table.")
    return {"agent": agent, "model": model, "attempts": attempts, "env": params.get("env"), "tasks": len(tasks)}


def where(params: dict, url: str) -> str:
    """Where a job's trials run: docker or modal. They run in Docker when the job asks for it or when the server, at
    `url`, is local."""
    return params.get("env") or ("docker" if urllib.parse.urlsplit(url).hostname in LOOPBACK else "modal")


def sweep(tag: str):
    """Asks Modal to end each sandbox tagged with a job's claim `tag` (its trials' and their verifiers'). Harbor's
    sandboxes live a day, so a Harbor that was killed leaves them running."""
    import modal
    try:
        app = modal.App.lookup(APP)
    except modal.exception.NotFoundError:
        return
    for sb in modal.Sandbox.list(app_id=app.app_id, tags={TAG: tag}):
        if sb.poll() is None:
            sb.terminate()


def command(job: dict, task: Path, out: Path, llm: str, key: str) -> tuple[list, dict]:
    """The Harbor command for a claimed job, built only from its checked parameters, and the whole environment Harbor
    runs with. `llm` is the server's model gateway and `key` the job's spend token. Trials of a coding agent reach
    only that gateway."""
    params, to = job["params"], urllib.parse.urlsplit(llm)
    on, trials = where(params, llm), min(params["attempts"] * params["tasks"], CONCURRENT)
    if on == "docker" and to.hostname in LOOPBACK:  # a container's own 127.0.0.1 is itself, not this machine
        to = to._replace(netloc=FROM_DOCKER + (f":{to.port}" if to.port else ""))
    argv = [*shlex.split(os.environ.get("HARBOR") or ""), "run", "-p", str(task), "--env", on, "-a",
            params["agent"], "-k", str(params["attempts"]), "-n", str(trials), "-o", str(out),
            "--job-name", job["id"], "--max-retries", "1", "--retry-include", RETRY, "-y"]
    if not os.environ.get("HARBOR"):  # the job image sets HARBOR to the Harbor it has installed
        argv[:0] = ["uvx", "--from", VERSION, "harbor"]
    if params["model"]:
        argv += ["-m", params["model"]]
    if on == "modal":
        argv += ["--ek", "labels=" + json.dumps({TAG: job["tag"]})]
    env = {**{k: os.environ[k] for k in ("PATH", "HOME", *MODAL) if k in os.environ}, "SWARMORY_JOB_KEY": ""}
    if params["agent"] in GATEWAY:
        route, base, name = GATEWAY[params["agent"]]
        argv += ["--ae", f"{base}={to.geturl()}{route}", "--ae", f"{name}=${{SWARMORY_JOB_KEY}}",
                 "--allow-environment-host", to.hostname]
        env["SWARMORY_JOB_KEY"] = key
    return argv, env


def report(folder: Path, code: int, log: Path) -> dict:
    """What the job's worker tells the server once Harbor has exited with `code`: the trials Harbor wrote in
    `folder`, or with none of them the end of its output."""
    each = trials(folder)
    errored = sum(bool(t["error"]) for t in each)
    passed = sum(not t["error"] and t["reward"] is not None and t["reward"] >= 1 for t in each)
    summary = {"trials": len(each), "passed": passed, "errored": errored, "each": each}
    if not each:
        return {"status": "failed", "summary": summary, "error":
                f"Harbor exited {code} with no trial results. Its output ends:\n"
                + log.read_text(errors="replace")[-3000:]}
    return {"status": "done", "summary": summary,
            "headline": f"{passed} passed, {len(each) - passed - errored} failed, {errored} errored of {len(each)}"}

