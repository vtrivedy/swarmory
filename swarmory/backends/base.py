"""What the server needs from wherever its workers run. A worker is one process tree that calls back to the server with
one token: an agent's life (swarmory.worker.agent) or one job (swarmory.worker.job). A backend knows a worker only by
its name, RUN/agent/ID or RUN/job/JID, so `start` and `stop` can be repeated, and a restarted server finds its workers
with `live`."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WorkerSpec:
    name: str              # "RUN/agent/ID" or "RUN/job/JID"
    env: dict              # SWARMORY_URL, one token and the worker's settings. Never a provider key.
    grants: tuple = ()     # an agent's --grant names, or a job's (jobs.SECRETS): on the local backend variables of the
                           # server's environment, on Modal the Modal secrets of an agent's names (a job's worker gets
                           # the Modal secret JOB_SECRET)
    allow_hosts: tuple = ()  # the hosts it may reach: the server's, plus the run's --allow
    home: str = ""         # its folder in the server's data, which survives a restart: an agent's runs/RUN/members/ID,
                           # a job's runs/RUN/jobs/JID
    image: str = ""        # an agent's image when its mission has an image.sh: what Backend.image returned

    @property
    def role(self) -> str:
        """"agent" or "job": a backend runs `python3 -m swarmory.worker.ROLE` with the Python that has swarmory."""
        return self.name.split("/")[1]


class Backend(Protocol):
    kind: str  # "local" or "modal": where its workers run, which an agent's starting prompt describes

    def start(self, spec: WorkerSpec) -> None:
        """Starts the worker, unless one of that name is live."""

    def live(self) -> frozenset:
        """The names of the workers running now."""

    def stop(self, name: str) -> None:
        """Stops the worker and all it started. Nothing happens if it is not running."""

    def image(self, script: str) -> str:
        """The agents' image with `script`, a mission's image.sh, run in it as root: its id, or "" where agents run on
        this machine and use its tools. It may take minutes. Raises what the build raised."""

    def check(self, harnesses: list, grants: list, allow: list) -> None:
        """Raises a ValueError, when a run is asked for, that names what this backend cannot give its agents."""
