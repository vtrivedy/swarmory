"""An agent's trace: what its coding agent did in each session, read from the session files agent.py sends the
server (runs.py keeps them in DATA/runs/RUN/traces/<member>/). A session file is the harness's own output, lines of
JSON, between two lines worker/agent.py writes: {"type": "swarmory", "event": "start", ...} with the harness's name and
the prompt the session got, and {"type": "swarmory", "event": "end", ...} with its exit status.

events() turns one session file into a list of events of one shape for every harness, each a dict with a `kind`. The
harness's file in swarmory/harness/ parses its own lines into them:

    start    the session began: prompt (what it was told, including the messages for it), resumed, harness, model
    init     the harness started: model, tools, MCP servers
    think    reasoning: text (empty when the provider withholds it), tokens (an estimate, when the harness gives one)
    say      text the agent wrote
    tool     a tool call: id, name, input (the arguments), parent (the subagent call it ran under, if any)
    result   a tool's result: id, text, error, exit (a command's exit code)
    user     text given to the agent mid-session that isn't a tool result, such as a subagent's prompt
    task     a background job or subagent: id, status, text
    todo     the agent's plan: items, each {text, done}
    compact  the conversation was compacted: tokens (its size before), trigger
    usage    a turn ended: input, cached, written, output tokens, and where the harness gives them duration_ms,
             turns, error
    error    an error the harness reported: text
    warn     a warning, a problem the harness carried on past: text
    note     anything else worth a line: text
    end      the session ended: status (exit code), stderr (its last lines, if it failed)
    other    an event the harness's parser doesn't know: type, text (the event, shortened)

Every event also has `at`, the time its line reached the server. Long text is cut (swarmory/harness/base.py),
and a Masker hides secrets in every string an event carries. """
import bisect
import json
import re

from swarmory import harness
from swarmory.harness.base import cut

from .policy import sha

MARK = b'"type": "swarmory"'  # in each of agent.py's two lines, as json.dumps writes them
KEYS = re.compile("|".join((
    r"sk-ant-[A-Za-z0-9_-]{16,}", r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}", r"lsv2_[a-z]{2,}_[A-Za-z0-9_]{16,}",
    r"gh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{20,}", r"AKIA[A-Z0-9]{16}", r"AIza[A-Za-z0-9_-]{35}",
    r"xox[abposr]-[A-Za-z0-9-]{10,}", r"pypi-[A-Za-z0-9_-]{20,}", r"\ba[ks]-[A-Za-z0-9]{16,}",
    r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")))
ASSIGNED = re.compile(r"(?i)\b([\w-]*(?:key|token|secret|password)\w*[\"']?\s*[:=]\s*[\"']?)([^\s\"',}]{8,})")
HEX32 = re.compile(r"\b[0-9a-f]{32}\b")


class Masker:
    """Hides secrets: the exact values in `values` (the server's keys and logins), any 32-hex string whose sha256 is in
    `hashes` (a member's run token or the view token, a set the run adds to as it mints tokens), strings shaped like
    common API keys and tokens, and the value of anything assigned to a name with key, token, secret or password in
    it."""

    def __init__(self, values=(), hashes=frozenset()):
        self.values = sorted({v for v in values if v and len(v) >= 8}, key=len, reverse=True)
        self.hashes = hashes

    def known(self, s: str) -> str:
        """s without the secrets this server knows, the values and the tokens. It guesses at nothing, so it is safe
        over the team's messages, whose text a guess would damage."""
        for v in self.values:
            s = s.replace(v, "[hidden]")
        return HEX32.sub(lambda m: "[hidden]" if sha(m[0]) in self.hashes else m[0], s)

    def __call__(self, s: str) -> str:
        return ASSIGNED.sub(lambda m: m[1] + "[hidden]", KEYS.sub("[hidden]", self.known(s)))


def masker(env, hashes) -> Masker:
    """A Masker for a run: the values of its environment's keys, tokens, secrets and logins (the strings inside a login
    that is JSON too), and `hashes`, the digests of every token the run has had."""
    values = [v for k, v in env.items() if re.search(r"KEY|TOKEN|SECRET|PASSWORD|AUTH", k)]
    for h in harness.HARNESSES:  # a login that is a JSON file: every string in it
        try:
            values += strings(json.loads(env.get(h.login) or "{}"))
        except ValueError:
            pass
    return Masker(values, hashes)


def strings(x) -> list:
    """Every string in a JSON value."""
    if isinstance(x, str):
        return [x]
    return [s for v in (x.values() if isinstance(x, dict) else x if isinstance(x, list) else ()) for s in strings(v)]


def mark(line: bytes):
    """One of agent.py's two lines around a session's output, or None."""
    if MARK not in line:
        return None
    try:
        e = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return e if isinstance(e, dict) and e.get("type") == "swarmory" else None


def events(raw: bytes, arrivals: list, mask) -> list:
    """The events of one session file. `arrivals` is [(offset, time)] for each chunk the server received, in
    order: an event's `at` is when the chunk holding its line's end arrived. A last line without its newline isn't read
    yet. The file's first line is agent.py's start line, which names the harness that parses the rest: a file without
    one has no events, and one whose harness this server does not know has only agent.py's start and end."""
    out, starts, whole = [], [o for o, _ in arrivals], raw.split(b"\n")[:-1]
    marks = [mark(line) for line in whole]
    now = [None]  # `at` of the line being read

    def own():
        """The harness's own lines, one at a time as its parser asks, with agent.py's lines made events on the way."""
        end = 0
        for line, m in zip(whole, marks):
            end += len(line) + 1
            now[0] = arrivals[max(0, bisect.bisect_left(starts, end) - 1)][1] if arrivals else None
            if m is None:
                yield line.decode(errors="replace")
            elif m.get("event") == "start":
                out.append({"kind": "start", "prompt": cut(m.get("prompt") or "", 20000), "resumed": m.get("resumed"),
                            "harness": m.get("harness"), "model": m.get("model"), "at": m.get("at") or now[0]})
            else:
                out.append({"kind": "end", "status": m.get("status"), "stderr": cut(m.get("stderr") or "", 2000),
                            "at": m.get("at") or now[0]})

    if not (marks and marks[0] and marks[0].get("event") == "start"):
        return []
    try:
        parse = harness.named(marks[0].get("harness")).parse
    except KeyError:
        def parse(lines):  # reads past the harness's lines, which leaves agent.py's two
            return [ev for _ in lines for ev in ()]
    for ev in parse(own()):
        out.append({**ev, "at": now[0]})
    return [masked(ev, mask) for ev in out]


def masked(x, mask):
    """x with `mask` over every string in it."""
    if isinstance(x, str):
        return mask(x)
    if isinstance(x, dict):
        return {k: masked(v, mask) for k, v in x.items()}
    if isinstance(x, list):
        return [masked(v, mask) for v in x]
    return x
