"""The verb table: every verb the server answers, at POST /api/<verb> for the server's and POST /r/RUN/api/<verb> for a
run's. Each verb names its action in policy.TABLE, says what it takes, and answers {"data": ..., "text": ...}: `text` is
what `swarmory` prints, written here so no client holds per-verb printing, and `data` is what `swarmory --json` prints
and what MCP returns. `swarmory mcp` makes its tools from this table (Verb.listing); the CLI has its own parser.

Some verbs need their client to take a step first: upload the files to attach, push the commit a job runs on, or work
out the tree of a folder. `get`, `login`, `trace` and `use` are the client's alone."""
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from swarmory import HOURS, WAKE_EVERY

from .jobs import described
from .policy import TABLE, sha
from .proxy import money
from .runs import new_run, state, view_token
from .team import STATUSES, NotFound

LOOK = {"readOnlyHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
SAME = {**WRITE, "idempotentHint": True}
NL = "\n    "  # a newline in text written by others, indented so no line of it can pass for a message of its own


@dataclass(frozen=True)
class Param:
    name: str
    type: str                # a JSON Schema type: string, integer, number, boolean, array or object
    doc: str
    required: bool = False
    enum: tuple = ()


@dataclass(frozen=True)
class Verb:
    name: str
    scope: str               # "server" or "run"
    action: str              # its row in policy.TABLE
    doc: str
    call: Callable           # a run verb's (run, Principal, request, gone); a server verb's (server, request, token)
    text: Callable           # (data, request): what `swarmory` prints
    params: tuple = ()
    hints: dict = field(default_factory=lambda: LOOK)

    def listing(self) -> dict:
        params = [{k: v for k, v in asdict(p).items() if k != "enum" or v} for p in self.params]
        return {"name": self.name, "scope": self.scope, "doc": self.doc, "params": params, "hints": self.hints,
                "who": sorted(TABLE[self.action])}


# ---- what `swarmory` prints

def left(deadline: float) -> str:
    return "About %d minutes left." % max(0, round((deadline - time.time()) / 60))


def size(n: int) -> str:
    return "%d B" % n if n < 1024 else "%.0f KB" % (n / 1024) if n < 1 << 20 else "%.1f MB" % (n / (1 << 20))


def line(m: dict, most: int = None) -> str:
    act, text = m["activity"], m["text"]
    text = (text[:most] + "…" if most and len(text) > most else text).replace("\n", NL)
    head = m["id"] + (" ↳" + m["reply_to"] if m["reply_to"] else "") + " " + m["conv"]
    files = "".join(" [%s %s, %s]" % (f["id"], f["name"].replace("\n", " "), size(f["size"]))
                    for f in m.get("files", ()))
    return "[%s] %s%s %s" % (head, "· " if act else "", m["author"] + ("" if act else ":"), (text + files).strip())


def issue_line(i: dict) -> str:
    title, who = i["title"].replace("\n", NL), ", ".join(i["assignees"]) or "unassigned"
    return "%s [%s] %s (%s)" % (i["id"], i["status"], title, who)


def chan(c: dict) -> str:
    return "#%-9s %s (%s)" % (c["id"], c["about"].replace("\n", NL), ", ".join(c["members"]))


def me_text(r: dict, req: dict) -> str:
    m, unread = r["me"], ["%s %d" % cn for cn in r["unread"].items() if cn[1]]
    role = "an operator" if m.get("operator") else "an agent" if m["kind"] == "agent" else "a person"
    return "\n".join([
        "You are %s (%s), %s in run %s." % (m["name"], m["id"], role, r["run"]),
        "Unread: %s. Run `swarmory read`." % ", ".join(unread) if unread else "Nothing unread.",
        "Your open issues:\n  " + "\n  ".join(map(issue_line, r["issues"])) if r["issues"]
        else "No open issues assigned to you.",
        left(r["deadline"]) + " Commands: `swarmory help`."])


def who_text(r: dict, req: dict) -> str:
    people = ["%-10s %s, %s%s" % (m["id"], m["name"], "an agent" if m["kind"] == "agent" else "a person",
                                  ", operator" if m.get("operator") else "") for m in r["members"]]
    return "\n".join(people + [chan(c) for c in r["channels"]])


def read_text(r: dict, req: dict) -> str:
    where = " in " + req["conv"] if req.get("conv") else ""
    none = "No %smessages%s." % ("" if req.get("all") else "unread ", where)
    more = ["(%d more unread: run `swarmory read` again)" % r["more"]] if r["more"] else []
    return "\n".join([*map(line, r["messages"])] or [none]) + "".join("\n" + x for x in more)


def joined_text(r: dict, req: dict, leave: bool = False) -> str:
    if "channel" in r:
        return chan(r["channel"])
    if leave:
        return "Left %s: it wakes you again once you comment, are assigned or @mentioned." % r["issue"]["id"]
    return "Following %s: its comments and changes wake you." % r["issue"]["id"]


def issue_text(r: dict, req: dict) -> str:
    i = r["issue"]
    body = [NL[1:] + i["body"].replace("\n", NL)] if i["body"] else []
    return "\n".join([issue_line(i), "opened by %s; followers: %s" % (i["author"], ", ".join(i["subscribers"])),
                      *body, *map(line, r["messages"])])


def sleep_text(r: dict, req: dict) -> str:
    """The wake, which is the agent's next prompt: each message cut at 300 characters, which bounds it."""
    if not r["woke"]:
        return "Nothing woke you."
    more = ["and %d more: swarmory read" % r["more"]] if r["more"] else []
    return "\n".join(["You were woken by:", *(line(m, 300) for m in r["reasons"]), *more, left(r["deadline"])])


def tokens_in(u: dict) -> int:
    return u["input"] + u["cached"] + u["written"]


def used_line(u: dict) -> str:
    return "%d, tokens in %s, out %s, %s%s" % (u["calls"], format(tokens_in(u), ","), format(u["output"], ","),
                                               money(u.get("usd") or 0),
                                               " and %d with no price (%s)" % (
                                                   u["unpriced"], ", ".join(u["unpriced_models"]))
                                               if u.get("unpriced") else "")


def spend_text(r: dict, req: dict) -> str:
    total = sum(u.get("usd") or 0 for u in r["by"].values())
    budget = " of a %s budget" % money(r["budget"]) if r.get("budget") is not None else ", with no budget"
    rows = ["  %-10s calls %s" % (who, used_line(u))
            for who, u in sorted(r["by"].items(), key=lambda kv: -(kv[1].get("usd") or 0))]
    return "\n".join(["Model calls in this run have cost %s%s." % (money(total), budget), *rows])


def job_line(j: dict) -> str:
    o = j["outcome"] or {}
    error = (o.get("error") or "").strip().splitlines()
    result = (o.get("headline") or "" if j["status"] == "done"
              else error[-1][:120] if j["status"] == "failed" and error else "")
    return "%s %-8s %-10s %s%s" % (j["id"], j["status"], j["by"], described(j), "  " + result if result else "")


def summary_lines(summary) -> list:
    """Harbor's summary, whatever it holds: a line per value, and a line per item of a list."""
    rows = []
    for key, value in (summary or {}).items():
        if isinstance(value, list):
            rows += ["  " + ("  ".join("%s %s" % kv for kv in item.items() if kv[1] is not None)
                             if isinstance(item, dict) else str(item)) for item in value]
        else:
            rows.append("%s: %s" % (key, value))
    return rows


def job_text(r: dict, req: dict) -> str:
    j, o = r["job"], r["job"]["outcome"] or {}
    rows = [job_line(j), "commit %s, tree %s" % (j["commit"], j["tree"])]
    rows += ["model calls: " + used_line(j["used"])] if j.get("used") else []
    rows += summary_lines(o.get("summary"))
    rows += ["its artifacts: `swarmory get %s`" % j["artifacts"]] if j["artifacts"] else []
    rows += [o["error"].replace("\n", NL)] if j["status"] == "failed" and o.get("error") else []
    return "\n".join(rows)


def queued_text(r: dict, req: dict) -> str:
    j = r["job"]
    return ("Queued %s: %s. Its result comes as a message in %s that @mentions you: end your turn and it wakes you."
            % (j["id"], described(j), j["to"]))


def jobs_text(r: dict, req: dict) -> str:
    on = req.get("tree") or req.get("path")
    return "\n".join(map(job_line, r["jobs"])) or ("No job in this run ran on this content (%s)." % str(on)[:40] if on
                                                   else "No jobs yet.")


def started_text(r: dict, req: dict) -> str:
    return "\n".join([f"run    {r['run']}: until {time.strftime('%H:%M %Z', time.localtime(r['deadline']))}",
                      f"url    {r['url']}",
                      f"watch  {r['view']}   (read-only: anyone with the link sees the whole run)"])


def runs_text(r: dict, req: dict) -> str:
    when = lambda x: time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(x["created"]))  # noqa: E731
    return "\n".join("%-33s %-15s %-8s %2d agents  %s" % (x["run"], x["mission"], x["state"], x["agents"], when(x))
                     for x in r["runs"]) or "No runs yet."


def status_text(r: dict, req: dict) -> str:
    short = lambda n: f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(n)  # noqa: E731
    out = [f"run {r['run']} ({r['mission']}): {r['state']}"
           + (f", {r['minutes_left']} min left" if r["state"] == "running" else ""), f"url {r['url']}"]
    out += [f"watch {r['view']}"] if r.get("view") else []
    out.append(f"{'':10}{'harness':13}{'model':28} {'worker':17}{'starts':>7}{'sessions':>9}{'calls':>7}"
               f"{'tokens in / out':>16}{'cost':>10}")
    used = [m["used"] or {} for m in r["members"]]
    for m, u in zip(r["members"], used):
        use = (f"{u['calls']:>7}{short(tokens_in(u)) + ' / ' + short(u['output']):>16}{money(u.get('usd') or 0):>10}"
               if u else "")
        blank = {k: "" if v is None else v for k, v in m.items()}
        out.append(f"{m['id']:10}{m['harness']:13}{m['model']:28} {blank['worker']:17}{blank['starts']:>7}"
                   f"{blank['sessions']:>9}{use}".rstrip())
    spent, unpriced = (sum(u.get(k) or 0 for u in used) for k in ("usd", "unpriced"))
    models = ", ".join(sorted({m for u in used for m in u.get("unpriced_models", ())}))
    out.append(f"model calls cost {money(spent)} at list prices"
               + (f", of a {money(r['budget'])} budget" if r.get("budget") is not None else "")
               + (f", and {unpriced} calls have no price ({models})" if unpriced else ""))
    every = r.get("wake_every")
    out.append(f"periodic wake: {every:g} s after an agent's last session, if nothing woke it sooner" if every else
               "no periodic wake: an agent sleeps until something wakes it")
    return "\n".join(out)


# ---- what each verb does

def team(method: str) -> Callable:
    """A Team method as a run verb."""
    return lambda run, who, req, gone: getattr(run.team, method)(who.member, req)


def clock(run, out: dict) -> dict:
    """An answer with the run and its deadline, which `swarmory` and a wake tell the time left from."""
    return {**out, "run": run.id, "deadline": run.config["deadline"]}


def jobs(method: str) -> Callable:
    """A Jobs method as a run verb, each job in its answer with what its trials' model calls used."""
    def call(run, who, req, gone):
        out, used = getattr(run.jobs, method)(who.member, req), run.proxy.totals(jobs=True)
        for job in out.get("jobs") or ([out["job"]] if out.get("job") else []):
            job["used"] = used.get(job["id"])
        return out
    return call


def find(server, req: dict):
    run = server.runs.get(req.get("run") if isinstance(req.get("run"), str) else "")
    if not run:
        raise NotFound(f"there is no run {req.get('run')!r}: `swarmory runs` lists them")
    return run


def start(server, req: dict, token: str) -> dict:
    config, view = new_run(req, server.backend, os.environ, token, lambda m: server.litellm.find(m)[0])
    run, url = server.runs.create(config), f"{server.url}/r/{config['run']}"
    return {"run": run.id, "url": url, "view": f"{url}/view#{view}", "deadline": config["deadline"],
            "members": [m["id"] for m in config["members"]]}


def listing(server, req: dict, token: str) -> dict:
    runs = [{"run": rid, "mission": c["mission"], "state": state(c), "created": c["created"],
             "deadline": c["deadline"], "agents": sum(m["kind"] == "agent" for m in c["members"])}
            for rid, c in server.runs.configs()]
    return {"runs": sorted(runs, key=lambda r: -r["created"])}


def status(server, req: dict, token: str) -> dict:
    run = find(server, req)
    url, view = f"{server.url}/r/{run.id}", view_token(token, run.id)
    return {**run.status(server.backend.live()), "url": url,
            **({"view": f"{url}/view#{view}"} if sha(view) == run.config["view_sha256"] else {})}


def stop(server, req: dict, token: str) -> dict:
    run = find(server, req)
    run.stop()
    server.supervisor.tick()  # its workers are stopped before the answer
    return {"run": run.id, "state": run.state()}


RUN = Param("run", "string", "the run's id, from start or runs", True)
CONV = "a channel (general), a member (your DM with them), an issue (i4), or a DM between two others (dm:ada:grace)"
LIMIT = lambda most, default: Param("limit", "integer", f"at most this many, 1 to {most} ({default})")  # noqa: E731
VERBS = {v.name: v for v in [
    Verb("start", "server", "server", "Starts a run: a team of coding agents on a mission, until its hours are up or "
         "it is stopped. Its agents start once its repo has a main.", start, started_text, (
             Param("mission", "string", "the mission's name", True),
             Param("team", "array", "one {harness, model?, role?, name?, args?} per agent", True),
             Param("hours", "number", f"how long the run lasts ({HOURS})"),
             Param("budget", "number", "the most its model calls may cost, in dollars at list prices"),
             Param("me", "string", "your id in the run"),
             Param("grant", "array", "what every agent gets: variables of a local server's environment, or Modal "
                   "secrets"),
             Param("allow", "array", "on Modal, hosts agents may reach too"),
             Param("jobs", "boolean", "members may ask for Harbor jobs"),
             Param("subscription", "boolean", "call models with the server's subscription logins, not API keys"),
             Param("wake_every", "number", f"seconds a sleeping agent no one wakes sleeps before it wakes to find work "
                   f"({WAKE_EVERY}); 0 never, else 60 to 86400")),
         WRITE),
    Verb("runs", "server", "server", "Every run, newest first, with its state: running, ended or stopped.", listing,
         runs_text),
    Verb("status", "server", "server", "A run's state, minutes left and budget, and each member's worker, sessions, "
         "model calls, tokens and cost.", status, status_text, (RUN,)),
    Verb("stop", "server", "server", "Stops a run's workers for good. The run stays readable.", stop,
         lambda r, req: f"Stopped {r['run']}. It stays readable.", (RUN,), {**SAME, "destructiveHint": True}),

    Verb("me", "run", "team", "Who you are, your unread message counts by conversation, and your open issues.",
         lambda run, who, req, gone: clock(run, run.team.me(who.member, req)), me_text),
    Verb("who", "run", "team", "The team's members and its channels, with each channel's members.", team("who"),
         who_text),
    Verb("read", "run", "team", "Unread messages, everywhere or in one conversation, oldest first, and marks them "
         "read. With all: the latest messages there, read or not, marking nothing.", team("read"), read_text, (
             Param("conv", "string", CONV), Param("all", "boolean", "read or not, marking nothing"),
             LIMIT(1000, 100)), WRITE),
    Verb("send", "run", "team", "Posts in a channel, DMs a member, or comments on an issue. It wakes the DM's other "
         "member, whoever it @mentions (@all, @channel), the author it replies to, and an issue's followers.",
         team("send"), lambda r, req: "Sent %s to %s." % (r["message"]["id"], r["message"]["conv"]), (
             Param("to", "string", CONV, True),
             Param("text", "string", "the message; it can be left out when files are attached"),
             Param("reply_to", "string", "a message id in the same conversation"),
             Param("files", "array", "file ids to attach; `swarmory send --file PATH` uploads them first")),
         WRITE),
    Verb("channel", "run", "team", "Makes a channel, with you in it.", team("channel"),
         lambda r, req: chan(r["channel"]),
         (Param("name", "string", "a-z, 0-9 and -, up to 40", True), Param("about", "string", "what it is for")),
         WRITE),
    Verb("join", "run", "team", "Joins a channel, or follows an issue so its comments and changes wake you.",
         team("join"), joined_text, (Param("name", "string", "a channel or an issue id", True),), SAME),
    Verb("leave", "run", "team", "Leaves a channel, or stops an issue's wakes until you comment, are assigned or are "
         "@mentioned there.", team("leave"), lambda r, req: joined_text(r, req, leave=True),
         (Param("name", "string", "a channel or an issue id", True),), SAME),
    Verb("issues", "run", "team", "The board, newest first: open issues (todo and doing), or those with status.",
         team("issues"), lambda r, req: "\n".join(map(issue_line, r["issues"])) or "No issues.", (
             Param("status", "string", "only issues with this status", enum=STATUSES),
             Param("mine", "boolean", "only issues assigned to you"))),
    Verb("issue", "run", "team", "One issue and its whole thread.", team("issue"), issue_text,
         (Param("id", "string", "the issue, like i4", True),)),
    Verb("new", "run", "team", "Opens an issue in todo. Its author, its assignees and whoever its body @mentions "
         "follow it.", team("new"), lambda r, req: issue_line(r["issue"]), (
             Param("title", "string", "the title", True), Param("body", "string", "the description"),
             Param("assignees", "array", "member names; me is you")), WRITE),
    Verb("assign", "run", "team", "Assigns members to an issue, or unassigns them.", team("assign"),
         lambda r, req: issue_line(r["issue"]), (
             Param("id", "string", "the issue", True), Param("add", "array", "member names; me is you"),
             Param("remove", "array", "member names; me is you")), WRITE),
    Verb("move", "run", "team", "Moves an issue to a status, with an optional note in its thread.", team("move"),
         lambda r, req: issue_line(r["issue"]), (
             Param("id", "string", "the issue", True), Param("status", "string", "its new status", True, STATUSES),
             Param("note", "string", "why")), WRITE),
    Verb("sleep", "run", "team", "Sleeps until someone DMs, @mentions or replies to you, or an issue you follow gets a "
         "comment or a change, up to timeout seconds. Returns the messages that woke you.",
         lambda run, who, req, gone: clock(run, run.team.sleep(who.member, req, gone)), sleep_text,
         (Param("timeout", "number", "seconds, at most 300 (300)"),), WRITE),
    Verb("spend", "run", "team", "What the run's model calls have cost, by member, and the run's budget.",
         lambda run, who, req, gone: {"run": run.id, "budget": run.config.get("budget"), "by": run.proxy.totals()},
         spend_text),

    Verb("job", "run", "job.submit", "Has the run do a Harbor job on a committed folder, in a run started with "
         "jobs: Harbor's params, and its result as a message in `to`.", jobs("submit"), queued_text, (
             Param("path", "string", "the folder, as the commit has it", True),
             Param("commit", "string", "the commit; `swarmory job` pushes HEAD and sends it", True),
             Param("params", "object", "Harbor's parameters"),
             Param("hours", "number", f"the longest it may run ({HOURS}), never past the run's end"),
             Param("budget", "number", "the most its model calls may cost, in dollars"),
             Param("to", "string", "where its result is posted (general)")), WRITE),
    Verb("jobs", "run", "job.read", "The run's jobs, newest first: who asked, the commit and folder it runs "
         "on, its status, its outcome, and what its trials' model calls used. With tree, or commit and path: only "
         "the jobs that ran on exactly that folder content.", jobs("list"), jobs_text, (
             Param("mine", "boolean", "only jobs you asked for"), LIMIT(1000, 50),
             Param("tree", "string", "a folder's git tree id, 40 hex; `swarmory results PATH` works it out"),
             Param("commit", "string", "with path: a pushed commit"),
             Param("path", "string", "with commit: the folder in it"))),
    Verb("job-show", "run", "job.read", "One job, with Harbor's whole summary.", jobs("show"), job_text,
         (Param("id", "string", "the job, like j3", True),)),
    Verb("cancel", "run", "job.cancel", "Cancels a job that is queued or running: its work stops, and so do its "
         "trials' model calls.", jobs("cancel"), job_text, (Param("id", "string", "the job", True),), WRITE),
    Verb("finish", "run", "job.finish", "A job's worker reports how the job ended.", jobs("finish"),
         lambda r, req: f"{r['job']['id']} {r['job']['status']}", (
             Param("id", "string", "the job", True), Param("status", "string", "how it ended", True,
                                                           enum=("done", "failed")),
             Param("headline", "string", "Harbor's one line"), Param("summary", "object", "Harbor's summary"),
             Param("error", "string", "why it failed"), Param("artifacts", "string", "the file of its artifacts")),
         WRITE),
]}
