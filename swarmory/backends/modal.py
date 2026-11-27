"""Workers as Modal Sandboxes, and the Modal app that runs the server. Only this file imports `modal` (modal==1.6.3).

What the server's process needs, which deploy() puts in the deployed function's environment:

    SWARMORY_AGENT_IMAGE   the id of the image every agent runs in
    SWARMORY_JOB_IMAGE     the id of the image every job worker runs in, which has Harbor installed and on HARBOR

`Modal()` reads both, so `swarmory serve --backend modal` needs no more than that. The server's app is APP. The
server's public address is not a variable: the web function reads its own URL and gives it to `swarmory serve` as
--url.

`swarmory deploy` is `deploy()`. It builds the two worker images, deploys the app and returns the server's URL. A
deploy stops the old server's container rather than running it beside the new one, as two would write one data
directory. The server, and nothing else, gets the one Modal secret SERVER_SECRET: the provider keys or logins, and
SWARMORY_OPERATOR_TOKEN.

A worker is a Sandbox of the app WORKERS, not APP, so that a deploy stops no worker. It is tagged {run, role, id}, the
three parts of its name. An agent reaches only spec.allow_hosts and mounts only its own folder of the server's data
Volume, spec.home, at MOUNT. Its HOME is a folder of that, so its coding agent's conversations and its work tree
outlive its sandbox. The running server does not see those writes (a container sees another's commits only once it
starts), so an export takes sessions from the copies agents upload. A --grant NAME is the Modal secret NAME.
A job worker gets the one secret JOB_SECRET, the Modal token Harbor starts its trials' sandboxes with. Agents run a
mission's setup.sh themselves, so it can install only from hosts the run allows. A mission's image.sh, which the
operator's `start` sends, runs once as root in a layer on the agents' image (image()), with the network open."""
import base64
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import modal

from swarmory import PORT
from swarmory.backends.base import WorkerSpec
from swarmory.control_plane.jobs import PACKAGES
from swarmory.harness import HARNESSES, of

APP, WORKERS = "swarmory", "swarmory-workers"
AGENT_IMAGE, JOB_IMAGE = "SWARMORY_AGENT_IMAGE", "SWARMORY_JOB_IMAGE"
SERVER_SECRET = "swarmory-server"  # the server's: provider keys or logins, and SWARMORY_OPERATOR_TOKEN
JOB_SECRET = "swarmory-modal"  # what job workers get: Harbor's Modal token
CODE = "/opt/swarmory"  # where images hold the swarmory package
MOUNT = "/swarmory"  # an agent's folder of the Volume: its SWARMORY_DIR, with its HOME in it
DATA = "/data"  # where the server mounts the whole Volume
# caches stay in the sandbox, off the Volume, which would otherwise keep every download an agent makes
CACHE = "/tmp/cache"
CACHES = {"XDG_CACHE_HOME": CACHE, "PIP_CACHE_DIR": f"{CACHE}/pip", "UV_CACHE_DIR": f"{CACHE}/uv",
          "UV_TOOL_DIR": f"{CACHE}/uv-tools", "UV_TOOL_BIN_DIR": f"{CACHE}/bin",
          "UV_PYTHON_INSTALL_DIR": f"{CACHE}/uv-python", "npm_config_cache": f"{CACHE}/npm"}
# setup.sh runs on every start, so tools it installs here come back. A tool's links stay with the tool: links left on
# the Volume to a tool a restart lost would make `uv tool install` refuse to reinstall it
TAGS = ("run", "role", "id")
# (reserved, limit): Modal bills the larger of the reservation and what is used
SERVER_SIZE = {"cpu": (1.0, 2.0), "memory": (1024, 2048)}  # a core, so its LiteLLM starts in seconds
AGENT_SIZE = {"cpu": (0.25, 2.0), "memory": (1024, 4096)}
LONGEST = 24 * 3600 - 60  # a sandbox lives at most 24 hours
GRACE = 900  # how long past its deadline a worker's sandbox may take to stop
PACKAGE = Path(__file__).resolve().parents[1]  # swarmory, as this checkout has it
# what agents' machines have beyond Python and git: the usual tools, Node, and each coding agent's CLI
AGENT_APT = ("curl", "build-essential", "procps", "jq", "ripgrep", "less")
AGENT_RUN = ("curl -fsSL https://deb.nodesource.com/setup_22.x | bash -", "apt-get install -y nodejs",
             *(line for h in HARNESSES for line in h.install))

app = modal.App(APP)
volume = modal.Volume.from_name("swarmory-data", create_if_missing=True, version=2)  # the server's DATA


def found(secret: str) -> bool:
    try:
        modal.Secret.from_name(secret).hydrate()
        return True
    except modal.exception.NotFoundError:
        return False


def baked(image):
    """Copies the swarmory package into the image at CODE, and puts `swarmory` on PATH. It is the last layer, so a
    code change rebuilds only this."""
    image = image.add_local_dir(PACKAGE, f"{CODE}/{PACKAGE.name}", copy=True, ignore=["**/__pycache__"])
    return image.run_commands(f"printf '#!/bin/sh\\nPYTHONPATH={CODE} exec python3 -m swarmory.cli \"$@\"\\n' "
                              "> /usr/local/bin/swarmory && chmod 755 /usr/local/bin/swarmory")


def worker_image(apt=(), pip=(), run=()):
    """A worker's image: Python, git, the Debian packages, pip packages and shell lines named, and the code."""
    image = modal.Image.debian_slim(python_version="3.12").apt_install("git", "ca-certificates", *apt)
    image = image.pip_install(*pip) if pip else image
    return baked(image.run_commands(*run) if run else image)


def server_image():
    """The server and, in /opt/litellm, the LiteLLM it runs (control_plane/litellm.py), every package checked against
    its hash."""
    return baked(modal.Image.debian_slim(python_version="3.12").apt_install("git", "ca-certificates", "procps")
                 .add_local_file(PACKAGE / "control_plane" / "litellm.txt", "/opt/litellm.txt", copy=True)
                 .run_commands("python -m venv /opt/litellm",
                               "/opt/litellm/bin/pip install -q --no-deps --require-hashes -r /opt/litellm.txt"))


class Modal:
    kind = "modal"

    def __init__(self):
        self.images = {"agent": os.environ.get(AGENT_IMAGE), "job": os.environ.get(JOB_IMAGE)}
        self.app = modal.App.lookup(WORKERS, create_if_missing=True)
        self.names = {}  # sandbox id: its worker's name, or None if it is no worker. Tags never change.

    def sandboxes(self, name: str = None) -> list:
        """The running sandboxes of this app, or only those of the worker `name`."""
        tags = {"tags": dict(zip(TAGS, name.split("/")))} if name else {}
        return [sb for sb in modal.Sandbox.list(app_id=self.app.app_id, **tags) if sb.poll() is None]

    def start(self, spec: WorkerSpec):
        if self.sandboxes(spec.name):
            return
        command, env = ("python3", "-m", f"swarmory.worker.{spec.role}"), {**spec.env, "PYTHONPATH": CODE}
        # it ends GRACE past its deadline at the latest: the run's for an agent, its own limit for a job
        more = {"timeout": min(LONGEST, int(float(spec.env["SWARMORY_DEADLINE"]) - time.time()) + GRACE)}
        if spec.allow_hosts:
            more["outbound_domain_allowlist"] = list(spec.allow_hosts)
        if spec.role == "agent":  # its log and HOME are in its folder of the Volume, what it installs there on PATH
            # it mounts what the Volume had committed when it starts: the clone and prompt.md the server just wrote.
            # `sync` commits a v2 Volume and, unlike volume.commit(), reloads nothing, which open files would refuse
            subprocess.run(["sync", DATA], check=True)
            command = ("bash", "-c", f"mkdir -p {MOUNT}/home {CACHE}/bin && "
                       f"PATH={CACHE}/bin:{MOUNT}/home/.local/bin:$PATH "
                       f"exec {shlex.join(command)} >> {MOUNT}/agent.log 2>&1")
            env.update({**CACHES, **of({"harness": spec.env["SWARMORY_HARNESS"]}).sandboxed, "SWARMORY_DIR": MOUNT,
                        "HOME": f"{MOUNT}/home"})
            more.update(AGENT_SIZE, volumes={MOUNT: volume.with_mount_options(sub_path=spec.home)})
        secrets = spec.grants if spec.role == "agent" else (JOB_SECRET,)
        image = modal.Image.from_id(spec.image or self.images[spec.role])
        modal.Sandbox.create(*command, app=self.app, image=image, env=env,
                             secrets=[modal.Secret.from_name(s) for s in secrets],
                             tags=dict(zip(TAGS, spec.name.split("/"))), **more)

    def image(self, script: str) -> str:
        """Builds a layer on the agents' image that runs `script` with bash -e as root, with the network open. Modal
        caches layers by their command, so the same script on the same image builds once."""
        line = base64.b64encode(script.encode()).decode()
        image = modal.Image.from_id(self.images["agent"]).run_commands(
            f"echo {line} | base64 -d > /tmp/image.sh && cd /tmp && bash -e image.sh && rm image.sh")
        return image.build(self.app).object_id

    def live(self) -> frozenset:
        names = set()
        for sb in self.sandboxes():
            if sb.object_id not in self.names:
                tags = sb.get_tags()
                self.names[sb.object_id] = "/".join(tags[t] for t in TAGS) if all(t in tags for t in TAGS) else None
            names.add(self.names[sb.object_id])
        return frozenset(names - {None})

    def stop(self, name: str):
        """Commits an agent's Volume (`sync`), then terminates its sandbox. A job worker has no Volume."""
        for sb in self.sandboxes(name):
            try:
                if name.split("/")[1] == "agent":
                    sb.exec("sync", MOUNT, timeout=60).wait()
            except Exception as e:
                print(f"could not sync {name}: {e}", flush=True)
            sb.terminate(wait=True)

    def check(self, harnesses: list, grants: list, allow: list):
        for g in grants:
            if not found(g):
                raise ValueError(f"--grant {g}: there is no Modal secret {g!r}. Make it with `uvx modal secret "
                                 f"create {g} NAME=value ...`.")


# What follows is read twice: by `modal deploy`, in deploy()'s environment, and by the function's own container.
PASSED = {k: os.environ[k] for k in (AGENT_IMAGE, JOB_IMAGE) if os.environ.get(k)}


@app.function(image=server_image(), volumes={DATA: volume},
              secrets=[modal.Secret.from_name(SERVER_SECRET), modal.Secret.from_dict(PASSED)],
              min_containers=1, max_containers=1,  # one server: two would write one data directory
              timeout=24 * 3600, **SERVER_SIZE)
@modal.concurrent(max_inputs=1000)  # each held `swarmory wait` is a request
@modal.web_server(PORT, startup_timeout=120)
def server():
    """Runs `swarmory serve` on the data Volume. The container ends when the server does, so Modal starts another."""
    url = server.get_web_url().rstrip("/")  # this container's own handle, which Modal hydrated before calling this
    p = subprocess.Popen(  # the container's PYTHONPATH holds Modal's own package, which the backend imports
        [sys.executable, "-m", "swarmory.cli", "serve", "--backend", "modal", "--host", "0.0.0.0", "--port", str(PORT),
         "--data", DATA, "--url", url],
        cwd=CODE, env={**os.environ, "PYTHONPATH": f"{CODE}:{os.environ.get('PYTHONPATH', '')}",
                       "SWARMORY_PROXY_LITELLM": "/opt/litellm/bin/litellm"})
    threading.Thread(target=lambda: os._exit(p.wait() or 1), daemon=True).start()


def deploy() -> str:
    """Builds the agents' image and the job workers', which has Harbor, deploys the app with their ids in the server's
    environment, and returns the server's URL."""
    if not found(SERVER_SECRET):
        raise ValueError(f"There is no Modal secret {SERVER_SECRET}. Make it with `uvx modal secret create "
                         f"{SERVER_SECRET} SWARMORY_OPERATOR_TOKEN=$TOKEN ANTHROPIC_API_KEY=...`, with a TOKEN "
                         "you keep (`openssl rand -hex 16`).")
    target = modal.App.lookup(WORKERS, create_if_missing=True)
    print("building the images (the first build takes a few minutes)...", flush=True)
    with modal.enable_output():
        built = {AGENT_IMAGE: worker_image(AGENT_APT, run=AGENT_RUN).build(target).object_id,
                 JOB_IMAGE: worker_image(pip=PACKAGES).env({"HARBOR": "harbor"}).build(target).object_id}
    subprocess.run([sys.executable, "-m", "modal", "deploy", "--strategy", "recreate", "-m", __name__], check=True,
                   env={**os.environ, **built})
    return modal.Function.from_name(APP, "server").get_web_url().rstrip("/")
