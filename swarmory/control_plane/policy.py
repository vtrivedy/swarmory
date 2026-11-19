"""Who may do what: the five kinds of token, and the one table that decides every request.

    operator   the person who runs the server, and their own clients: a member of every run, as `swarmory start` made it
    member     one agent
    job        one job's worker: it reads its own job and input, uploads its artifacts and finishes its own job
    spend      one job's trials: model calls through /llm, billed to that job, until the job ends
    view       the watch link: read-only, and it shows the whole run

server.py works out the Principal a request's token names, and calls authorize() with the action its route or verb
names (verbs.py names each verb's). Nothing else decides a permission. The server mints a member's token when it starts
the member's worker, and a job's two tokens when it starts the job's worker."""
import hashlib
from dataclasses import dataclass

ANY, OWN = "any", "own"  # OWN: a member's own job (it asked for it), or a job or spend token's own job
TABLE = {
    "server": {"operator": ANY},                       # start, list, status and stop runs, and export one
    "team": {"operator": ANY, "member": ANY},          # chat, the board, wait, the run's spend
    "view": {"view": ANY},                             # what the viewer polls, which holds every DM
    "git": {"operator": ANY, "member": ANY},
    "file.upload": {"operator": ANY, "member": ANY, "job": ANY},
    "file.get": {"operator": ANY, "member": ANY, "view": ANY},  # a member's, only as team.may_get() says
    "trace.upload": {"operator": ANY, "member": ANY},
    "llm": {"operator": ANY, "member": ANY, "spend": ANY},
    "job.submit": {"operator": ANY, "member": ANY},
    "job.read": {"operator": ANY, "member": ANY, "job": OWN},
    "job.cancel": {"operator": ANY, "member": OWN},
    "job.input": {"job": OWN},
    "job.finish": {"job": OWN},                        # the server itself fails a job whose worker is gone
}


class Denied(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    kind: str           # a key of the rows above
    member: str = None  # who acts or pays: a member's id, or for a job or spend token whoever asked for the job
    job: str = None     # a job or spend token's job


def sha(token: str) -> str:
    """A token's digest, which is all the server keeps of it."""
    return hashlib.sha256(token.encode()).hexdigest()


def authorize(who, action: str, owner: str = None, job: str = None) -> Principal:
    """`who` (a Principal, or None for no good token) may do `action`, or Denied. `owner` and `job` say whose thing
    the request touches, for the actions a token may do only to its own."""
    rule = TABLE[action].get(who.kind) if who else None
    if rule == OWN:
        mine, theirs = (who.job, job) if who.kind in ("job", "spend") else (who.member, owner)
        rule = ANY if mine is not None and mine == theirs else None
    if rule != ANY:
        raise Denied(f"a {who.kind} token may not do that ({action})" if who else "unknown token")
    return who
