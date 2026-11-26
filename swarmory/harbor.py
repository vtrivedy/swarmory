"""Harbor, which runs every job. A job runs one agent some number of times on a Harbor task folder, or on each task in
a folder of them, in Docker containers on the server's machine or in Modal sandboxes, and reports each trial's
reward. A local server's trials run in Docker by default, where they can reach it, and any other server's on Modal.

Nothing in the task folder chooses the agent, the network or a credential. The parameters choose the agent: one of
Harbor's built in, or a harness, a committed folder with an executable `run`, which one fixed adapter
(worker/harbor_adapter/committed.py) runs inside each trial's sandbox, never in Harbor's own process. A job is
refused, when it is asked for, unless each task.toml is sealed: the environment and the verifier have no network, so
a model agent's trials reach only the server's model gateway, which the Harbor command adds. A `${...}` value
in an env table is refused too, because Harbor fills those from the environment of the process that runs it, and so is
a docker-compose.yaml anywhere in the folder (Harbor reads one from a verifier's tests/ too), which Docker fills the
same way and which could unseal it. The job worker (swarmory/worker/job.py) runs Harbor with a listed environment:
PATH, HOME, the Modal token Harbor starts sandboxes with, the job's spend token, and for a harness the adapter's folder
as PYTHONPATH."""
import copy
import json
import os
import re
import shlex
import tomllib
import urllib.parse
from pathlib import Path

VERSION = "harbor[modal]==0.27.0"
# The gateway's routes, as every agent that takes a model gets them: each route, the base-URL settings agents read
# for it, and the key setting, which carries the job's spend token
GATEWAY = (("/anthropic", ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_BASE"), "ANTHROPIC_API_KEY"),
           ("/openai/v1", ("OPENAI_BASE_URL", "OPENAI_API_BASE"), "OPENAI_API_KEY"),
           ("/gemini", ("GOOGLE_GEMINI_BASE_URL",), "GEMINI_API_KEY"))
MODEL_FREE = ("oracle", "nop")  # Harbor's agents that take no model
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")  # a Harbor agent's name, such as claude-code or terminus-2
OPTION = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}=.{0,2000}", re.S)  # one --ak, --ae or --ek: KEY=VALUE
# Harbor's environment options a job may set. The rest could mount a credential (secrets, registry_secret, volumes) or
# move the sandboxes out of the app and tags swarmory ends them by (app_name, labels, auto_labels).
ENV_OPTIONS = ("keepalive", "region", "modal_vm_runtime", "sandbox_timeout_secs", "sandbox_idle_timeout_secs",
               "dind_image", "auto_mounting")
TIMEOUTS = ("timeout_multiplier", "agent_timeout_multiplier", "verifier_timeout_multiplier",
            "agent_setup_timeout_multiplier", "environment_build_timeout_multiplier")
ADAPTER = Path(__file__).parent / "worker" / "harbor_adapter"  # Harbor's PYTHONPATH for a harness: this folder alone
COMPOSE = "docker-compose.yaml"
CONCURRENT = 8  # the trials one job runs at once unless it asks for another number
MOST_CONCURRENT = 64
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

    if not isinstance(doc.setdefault("environment", {}), dict):
        raise ValueError(f"{name}: environment is not a table")
    shut(doc["environment"])
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


def options(params: dict, flag: str, allowed=None, taken=()) -> list:
    """A job's repeated --ak, --ae or --ek values: KEY=VALUE strings, each with a literal value. Harbor fills a ${...}
    from the job worker's environment, which holds its credentials."""
    values = params.get(flag) or []
    values = [values] if isinstance(values, str) else values
    if not isinstance(values, list) or not all(isinstance(v, str) and OPTION.fullmatch(v) for v in values):
        raise ValueError(f"--{flag} takes KEY=VALUE, once per option")
    for v in values:
        key = v.split("=", 1)[0]
        if "${" in v:
            raise ValueError(f"--{flag} {key}: a value may not hold ${{...}}, which Harbor fills from the job "
                             "worker's credentials. Write the value itself.")
        if allowed is not None and key not in allowed:
            raise ValueError(f"--{flag} {key} is not an environment option a job may set: {', '.join(allowed)}")
        if key in taken:
            raise ValueError(f"--{flag} {key} is set by swarmory: {', '.join(taken)}")
    return values


def number(params: dict, key: str, kind, low, high):
    value = params.get(key)
    if value is None:
        return None
    try:
        value = kind(value)
    except (TypeError, ValueError):
        value = None
    if isinstance(value, bool) or value is None or not low <= value <= high:
        raise ValueError(f"{key} is a number from {low} to {high}")
    return value


def check(params: dict, inputs, url: str, harness=None) -> dict:
    """On the server, before the job is queued: the parameters as the job records them, or a ValueError that
    says what is wrong with them or with the folder. `url` is the server's address. `harness` is the committed
    folder the agent names, when it names one rather than one of Harbor's agents."""
    extra = params.keys() - {"agent", "model", "attempts", "env", "concurrent", "ak", "ae", "ek", *TIMEOUTS}
    if extra:
        raise ValueError(f"a harbor job takes --agent, --model, --attempts, --env, --concurrent, --ak, --ae, --ek and "
                         f"--{', --'.join(t.replace('_', '-') for t in TIMEOUTS)}, not {', '.join(sorted(extra))}")
    if params.get("env") not in (None, *ENVS):
        raise ValueError("env is docker, to run trials on the server's machine, or modal")
    agent, model = params.get("agent"), params.get("model")
    if harness is None and not (isinstance(agent, str) and NAME.fullmatch(agent)):
        raise ValueError("agent is one of Harbor's agents, such as oracle, nop, claude-code, codex, gemini-cli or "
                         "terminus-2, or a committed folder in the repo (not the repo itself) that holds an "
                         "executable `run`")
    if harness is not None and "run" not in harness.names():
        raise ValueError(f"the agent folder {agent} has no `run`: the executable each trial runs")
    modeled = agent not in MODEL_FREE
    if modeled != (model is not None) or model is not None and not (
            isinstance(model, str) and len(model) <= 100 and re.fullmatch(r"[\w.:/@-]+", model)):
        raise ValueError("model is a model name, which every agent but oracle and nop needs")
    attempts = number(params, "attempts", int, 1, 1000) or 1
    concurrent = number(params, "concurrent", int, 1, MOST_CONCURRENT)
    timeouts = {t: number(params, t, float, 0.1, 100) for t in TIMEOUTS if params.get(t) is not None}
    ak = options(params, "ak", taken=("dir",) if harness is not None else ())
    ae = options(params, "ae", taken=tuple(k for _, bases, key in GATEWAY for k in (*bases, key)))
    ek = options(params, "ek", allowed=ENV_OPTIONS)
    # with no env, a local server's trials run in Docker, so only trials sent to Modal cannot reach it
    if modeled and params.get("env") == "modal" and urllib.parse.urlsplit(url).hostname in LOOPBACK:
        raise ValueError(LOCAL)
    names = inputs.names()
    tasks = task_files(names)
    if not tasks:
        raise ValueError("the folder is not a Harbor task or a folder of them: it has no task.toml")
    if compose := next((n for n in names if n == COMPOSE or n.endswith("/" + COMPOSE)), None):
        raise ValueError(f"{compose}: a job's task runs as one container, which its Dockerfile builds. Docker fills a "
                         "compose file's ${...} from the job worker's environment, and a compose service can leave "
                         "the sealed network. Put what the task needs, such as an MCP server, in its Dockerfile.")
    for name in tasks:
        try:
            doc = tomllib.loads(inputs.read(name).decode())
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"{name} is not TOML: {e}") from None
        if seal(doc, name) != doc:
            raise ValueError(f'{name} is not sealed. A job\'s trials get no network but the model gateway: set '
                             f'[environment] network_mode = "{SEALED}", and take network_mode, allowed_hosts '
                             "and allow_internet out of every other table.")
    return {"agent": agent, "model": model, "attempts": attempts, "env": params.get("env"), "concurrent": concurrent,
            **timeouts, "ak": ak, "ae": ae, "ek": ek, "tasks": len(tasks)}


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


def command(job: dict, task: Path, out: Path, llm: str, key: str, harness: Path = None) -> tuple[list, dict]:
    """The Harbor command for a claimed job, built only from its checked parameters, and the whole environment Harbor
    runs with. `llm` is the server's model gateway and `key` the job's spend token; `harness` is the job's agent
    folder, if it has one. Trials of a coding agent or a harness reach only that gateway: a harness gets both its
    routes."""
    params, to = job["params"], urllib.parse.urlsplit(llm)
    on = where(params, llm)
    trials = params.get("concurrent") or min(params["attempts"] * params["tasks"], CONCURRENT)
    if on == "docker" and to.hostname in LOOPBACK:  # a container's own 127.0.0.1 is itself, not this machine
        to = to._replace(netloc=FROM_DOCKER + (f":{to.port}" if to.port else ""))
    argv = [*shlex.split(os.environ.get("HARBOR") or ""), "run", "-p", str(task), "--env", on, "-a",
            "committed:Committed" if harness else params["agent"], "-k", str(params["attempts"]), "-n", str(trials),
            "-o", str(out), "--job-name", job["id"], "--max-retries", "1", "--retry-include", RETRY, "-y"]
    if not os.environ.get("HARBOR"):  # the job image sets HARBOR to the Harbor it has installed
        argv[:0] = ["uvx", "--from", VERSION, "harbor"]
    if params["model"]:
        argv += ["-m", params["model"]]
    for t in TIMEOUTS:
        if params.get(t) is not None:
            argv += ["--" + t.replace("_", "-"), str(params[t])]
    for flag in ("ak", "ae", "ek"):
        for value in params.get(flag) or ():
            argv += ["--" + flag, value]
    if on == "modal":
        argv += ["--ek", "labels=" + json.dumps({TAG: job["tag"]})]
    env = {**{k: os.environ[k] for k in ("PATH", "HOME", *MODAL) if k in os.environ}, "SWARMORY_JOB_KEY": ""}
    if harness:
        argv += ["--ak", f"dir={harness}"]
        env["PYTHONPATH"] = str(ADAPTER)
    routes = GATEWAY if params["agent"] not in MODEL_FREE else ()
    for route, bases, name in routes:
        argv += [a for base in bases for a in ("--ae", f"{base}={to.geturl()}{route}")]
        argv += ["--ae", f"{name}=${{SWARMORY_JOB_KEY}}"]
    if routes:
        argv += ["--allow-environment-host", to.hostname]
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

