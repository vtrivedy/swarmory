"""A run's gateway, inside the server: every model call a member makes comes here with the member's run token, never a
key. The gateway forwards the call upstream with the real key, reads the tokens it used from the response (streamed or
not), prices them from LiteLLM's price table (litellm.py), and writes them to the run's ledger.jsonl. It forwards only
the routes in ROUTES: Anthropic's Messages, OpenAI's Responses and Chat Completions, and Gemini's generateContent,
streamGenerateContent and countTokens, so a coding agent that speaks any of them can use it. A model of another
provider than the route's, named <provider>/<model> or by a bare name the price table lists under that provider, goes
to LiteLLM, which translates the call. Each call is refused once the run or its job has spent its budget. When a
provider answers a member's call with a rate limit, the gateway notes until when, for the member's worker to pause.

litellm.py names the variables with the providers' API keys and upstreams. A run with subscriptions calls with the
person's logins instead, the way each harness calls when it holds its login itself: each harness's file in
swarmory/harness/ names the variable with its login and says how a logged-in call is signed and where it goes. A key
or login that isn't set turns that API off.
"""
import http.client
import json
import math
import os
import re
import threading
import time
import zlib
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from swarmory.harness import HARNESSES

from .litellm import LiteLLM, price, upstream
from .team import replay

USED = ("input", "cached", "written", "output")
FIELDS = {  # route: the request fields a translated call keeps; LiteLLM reads more (other upstreams, keys, billing)
    "v1/messages": {"model", "messages", "system", "max_tokens", "stream", "temperature", "top_p", "top_k",
                    "stop_sequences", "tools", "tool_choice", "thinking", "output_config"},
    "v1/messages/count_tokens": {"model", "messages", "system", "tools", "tool_choice", "thinking"},
    "v1/responses": {"model", "input", "instructions", "tools", "tool_choice", "stream", "temperature", "top_p",
                     "max_output_tokens", "reasoning", "text", "parallel_tool_calls", "store", "include",
                     "previous_response_id", "prompt_cache_key", "truncation"},
    "v1/chat/completions": {"model", "messages", "tools", "tool_choice", "stream", "stream_options", "temperature",
                            "top_p", "max_tokens", "max_completion_tokens", "stop", "response_format",
                            "reasoning_effort", "parallel_tool_calls", "seed"},
}  # Gemini's routes are never translated: a Gemini agent names only Gemini models
ROUTES = {  # (provider, method, path) -> whether it is a model call, recorded in the ledger
    ("anthropic", "POST", "v1/messages"): True,
    ("anthropic", "POST", "v1/messages/count_tokens"): False,
    ("anthropic", "GET", "v1/models"): False,
    ("openai", "POST", "v1/responses"): True,
    ("openai", "POST", "v1/chat/completions"): True,
    ("openai", "GET", "v1/models"): False,
    ("gemini", "POST", "v1beta/models/*:generateContent"): True,  # *: the model, which Gemini names in the path
    ("gemini", "POST", "v1beta/models/*:streamGenerateContent"): True,
    ("gemini", "POST", "v1beta/models/*:countTokens"): False,
    ("gemini", "GET", "v1beta/models"): False,
}
DAY = 24 * 3600
LIMITED = {429, 503, 529}  # an upstream's statuses for a rate limit or overload: try again later
KEY_HEADER = {"anthropic": "x-api-key", "openai": "authorization", "gemini": "x-goog-api-key"}
HOP = {"host", "content-length", "connection", "transfer-encoding", "accept-encoding", *KEY_HEADER.values(),
       "keep-alive", "proxy-connection", "te", "upgrade", "date", "server"}


def add(by: dict, row: dict, key: str = "who"):
    """One ledger row into calls, tokens and dollars by member, or with key "job" by job; a row without one counts
    for none. A call with no price adds no dollars and counts as unpriced, its model named, so a total never passes it
    off as free."""
    if row.get(key) is None:
        return
    s = by.setdefault(row[key], {**dict.fromkeys(("calls", *USED, "unpriced"), 0), "usd": 0.0})
    s["calls"] += 1
    for k in USED:
        s[k] += row[k]
    s["usd"] += row.get("usd") or 0
    if row.get("usd") is None:
        s["unpriced"] += 1
        s["unpriced_models"] = sorted({*s.get("unpriced_models", ()), row.get("model") or "unnamed"})


def refuse(handler, provider: str, status: int, message: str):
    """An error in the caller's own API's format, so its harness shows the message."""
    if provider == "openai":
        body = {"error": {"message": message, "type": "proxy_error", "code": "proxy_error"}}
    elif provider == "gemini":
        body = {"error": {"code": status, "message": message, "status": "PROXY_ERROR"}}
    else:
        body = {"type": "error", "error": {"type": "proxy_error", "message": message}}
    data = json.dumps(body).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def length(headers):
    """A request's Content-Length (0 if it has none), or None for a chunked body or a bad header."""
    n = (headers.get("Content-Length") or "0").strip()
    return int(n) if n.isdecimal() and not headers.get("Transfer-Encoding") else None


def relay(send) -> bool:
    """send() to the caller: False if it has hung up. The rest is still read from upstream, to record its usage."""
    try:
        send()
        return True
    except OSError:
        return False


def plain(body: bytes, encoding: str) -> bytes:
    """A reply's bytes uncompressed. An upstream may compress a reply though the server asks for none. The
    server undoes gzip and deflate; any other encoding stays as it came."""
    if encoding.strip().lower() in ("gzip", "x-gzip", "deflate"):
        try:
            return zlib.decompress(body, 47)  # 47: a gzip or zlib header, whichever it has
        except zlib.error:
            pass
    return body


def usage(provider: str, body: bytes):
    """(model, tokens used) from a reply, one JSON object or a stream of `data:` events, whatever its Content-Type
    says: input (not from cache), cached, written (to cache) and output. OpenAI's and Gemini's input counts include
    the cached part; Anthropic's doesn't. Gemini's output is its answer and its thinking."""
    try:
        events = json.loads(body)
        events = events if isinstance(events, list) else [events]  # a list: Gemini's streamed parts, without SSE
    except (ValueError, RecursionError):
        events = []
        for line in (x[5:] for x in body.splitlines() if x.startswith(b"data:")):
            try:
                events.append(json.loads(line))
            except (ValueError, RecursionError):
                pass
    model, used = "", dict.fromkeys(USED, 0)
    for e in events:
        if not isinstance(e, dict):
            continue
        r = e.get("message") or e.get("response") or e
        r = r if isinstance(r, dict) else e
        model = r.get("model") or r.get("modelVersion") or model
        u = r.get("usage") or e.get("usage") or {}
        if provider == "gemini" and isinstance(e.get("usageMetadata"), dict):  # each part has the totals so far
            u = e["usageMetadata"]
            cached = u.get("cachedContentTokenCount") or 0
            used.update(input=(u.get("promptTokenCount") or 0) - cached, cached=cached,
                        output=(u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0))
        elif provider == "anthropic":
            split = u.get("cache_creation")
            if isinstance(split, dict) and split.get("ephemeral_1h_input_tokens"):  # writes to the hour-long cache
                used["written_1h"] = max(used.get("written_1h", 0), split["ephemeral_1h_input_tokens"])
            for key, field in (("input", "input_tokens"), ("cached", "cache_read_input_tokens"),
                               ("written", "cache_creation_input_tokens"), ("output", "output_tokens")):
                used[key] = max(used[key], u.get(field) or 0)
        elif u:  # Responses, or Chat Completions' prompt_ and completion_ names
            cached = (u.get("input_tokens_details") or u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            used.update(input=(u.get("input_tokens") or u.get("prompt_tokens") or 0) - cached, cached=cached,
                        output=u.get("output_tokens") or u.get("completion_tokens") or 0)
    return model, used


def delay(resp, body: bytes) -> float:
    """Seconds a limited upstream asks the caller to wait, from 5 to a day: its retry-after, in seconds or as an HTTP
    date; else from the error body, an object or Gemini's list of them: Gemini's RetryInfo retryDelay ("37s"), or the
    resets_in_seconds or resets_at (epoch seconds) of a ChatGPT login's limit, which sends no retry-after; else a
    minute. A value it can't read, such as an ISO date, counts as none."""
    after = (resp.getheader("retry-after") or "").strip()
    number = rb'"\s*:\s*"?(\d+(?:\.\d+)?)s?"?\s*[,}\]]'
    try:
        wait = float(after)
    except ValueError:
        try:
            wait = parsedate_to_datetime(after).timestamp() - time.time()
        except (TypeError, ValueError):
            if m := re.search(rb'"(retryDelay|resets_in_seconds|resets_at)' + number, body):
                wait = float(m[2]) - (time.time() if m[1] == b"resets_at" else 0)
            else:
                wait = 60.0
    return min(max(5.0, wait), DAY) if math.isfinite(wait) else 60.0


def money(usd: float) -> str:
    """Dollars to the cent, or to a hundredth of a cent under one cent, so a small budget doesn't read as $0.00."""
    return f"${usd:.4f}" if 0 < usd < 0.01 else f"${usd:,.2f}"


def uncached(req: dict) -> dict:
    """A request without Anthropic's cache_control marks, for a call LiteLLM translates to another provider: they are
    only hints there, and on a system prompt they make LiteLLM ask Gemini for a context cache, which fails. Marks sit
    on system, tool and content blocks; a tool's schema and a tool call's arguments are the caller's data and stay."""
    def blocks(value):
        if not isinstance(value, list):
            return value
        out = []
        for b in value:
            if isinstance(b, dict):
                b = {k: v for k, v in b.items() if k != "cache_control"}
                if b.get("type") == "tool_result" and "content" in b:
                    b["content"] = blocks(b["content"])
            out.append(b)
        return out

    req = {**req, **{k: blocks(req[k]) for k in ("system", "tools") if k in req}}
    if isinstance(req.get("messages"), list):
        req["messages"] = [{**m, "content": blocks(m.get("content"))} if isinstance(m, dict) else m
                           for m in req["messages"]]
    return req


class Proxy:
    def __init__(self, data, env, budget, litellm: LiteLLM, subscription: bool):
        """A run's gateway: its folder, the environment with the person's logins, its budget in dollars or None, the
        server's one LiteLLM, which holds the API keys, and whether it calls with the logins instead of the keys."""
        self.ledger, self.budget, self.litellm = Path(data) / "ledger.jsonl", budget, litellm
        self.subscription = subscription
        self.logins = {}  # with subscriptions, provider: (upstream, harness, Login) of the first harness that has one
        for h in reversed([h for h in HARNESSES if h.login]) if subscription else ():
            if login := env.get(h.login) and h.signin(env[h.login]):
                self.logins[h.provider] = (upstream(env, h.provider, login.upstream), h, login)
        self.lock = threading.Lock()
        self.by, self.by_job = {}, {}  # a restarted server carries on from what it recorded
        self.in_flight = {}  # call ticket -> (job_id or None, estimated usd)
        # member: {model: (limited at, limited until)} of each model a provider limited and that hasn't answered
        # since, and member: when a model last answered its call. A restart forgets them
        self.limits, self.answered = {}, {}
        replay(self.ledger, self.replayed)
        self.file = open(self.ledger, "a")

    def replayed(self, row: dict):
        """One ledger row read back at start: its spend, and a member's last answered call, which a restart keeps."""
        add(self.by, row)
        add(self.by_job, row, "job")
        if row.get("who") and not row.get("job"):
            self.answered[row["who"]] = max(self.answered.get(row["who"], 0), row.get("at") or 0)

    def totals(self, jobs: bool = False) -> dict:
        """Calls, tokens and dollars by member, or by job."""
        with self.lock:
            return {k: dict(v) for k, v in (self.by_job if jobs else self.by).items()}

    def limited(self, who) -> dict:
        """For member `who`: when a model last answered its call (answered_at), and of the models a provider limited
        that haven't answered since, when the last limit came (limited_at) and when the last one ends
        (limited_until)."""
        with self.lock:
            limits = self.limits.get(who, {}).values()
            out = {"answered_at": self.answered[who]} if who in self.answered else {}
            if limits:
                out.update(limited_at=max(at for at, _ in limits), limited_until=max(until for _, until in limits))
            return out

    def _over(self, job) -> str | None:
        run = sum(s["usd"] for s in self.by.values())
        run_flight = sum(usd for _, usd in self.in_flight.values())
        if self.budget is not None and run + run_flight >= self.budget:
            extra = "" if run >= self.budget else f" (plus ~{money(run_flight)} in flight)"
            return f"The run has spent {money(run)}{extra} of its {money(self.budget)} budget."
        if not job:
            return None
        used = self.by_job.get(job["id"], {})
        if job.get("tokens") and sum(used.get(k, 0) for k in USED) >= job["tokens"]:
            return f"Job {job['id']} has used its {job['tokens']:,} tokens."
        spent = used.get("usd", 0)
        job_flight = sum(usd for jid, usd in self.in_flight.values() if jid == job["id"])
        if job.get("budget") is not None and spent + job_flight >= job["budget"]:
            extra = "" if spent >= job["budget"] else f" (plus ~{money(job_flight)} in flight)"
            return f"Job {job['id']} has spent {money(spent)}{extra} of its {money(job['budget'])} budget."
        return None

    def over(self, job) -> str | None:
        """Why a call now would be refused: the run or the job has spent its budget, or the job its tokens."""
        with self.lock:
            return self._over(job)

    def reserve(self, job, entry, body_len: int) -> tuple[object | None, str | None]:
        with self.lock:
            if why := self._over(job):
                return None, why
            calls = sum(s["calls"] for s in self.by.values())
            avg = sum(s["usd"] for s in self.by.values()) / calls if calls else 0.0
            if job and (j := self.by_job.get(job["id"])) and j["calls"]:
                avg = j["usd"] / j["calls"]
            est = max(avg, (body_len / 4.0) * ((entry or {}).get("input_cost_per_token") or 0.0))
            ticket = object()
            self.in_flight[ticket] = (job["id"] if job else None, est)
            return ticket, None

    def handle(self, h, provider: str, rest: str, who, job: dict = None):
        """Forward one request on the BaseHTTPRequestHandler h, for member `who` (None: an unknown token), to
        `provider`'s `rest` path. A call for a job ({id, tokens, budget}) is recorded under the job too, and
        refused once the job has used its tokens or spent its budget."""
        why = self.over(job) if who else "Unknown run token."
        if why:
            # read the request first (up to 1 MB): a reply sent while the client is still uploading resets it
            h.rfile.read(min(length(h.headers) or 0, 1 << 20))
            return refuse(h, provider, 429 if who else 401, why)
        n = length(h.headers)
        if n is None:
            return refuse(h, provider, 411, "Send the request with a Content-Length.")
        if n > 32 << 20:
            return refuse(h, provider, 413, "Send a request of at most 32 MB.")
        body = h.rfile.read(n)
        route = rest.partition("?")[0].strip("/")
        named = re.fullmatch(r"(v1beta/models/)([^/:]+)(:\w+)", route) if provider == "gemini" else None
        route = named and f"{named[1]}*{named[3]}" or route
        model_call = ROUTES.get((provider, h.command, route))
        if model_call is None:
            return refuse(h, provider, 404, f"The server doesn't forward {h.command} {route}: it forwards only "
                          f"{', '.join(m + ' ' + r for p, m, r in ROUTES if p == provider)}.")
        try:
            req = json.loads(body) if body else None
        except (ValueError, RecursionError):
            req = None
        req = req if isinstance(req, dict) else None
        asked = req.get("model") if req and isinstance(req.get("model"), str) else named and named[2] or ""
        if route == "v1/chat/completions" and req and req.get("stream"):  # a stream gives its usage only if asked
            options = req.get("stream_options") if isinstance(req.get("stream_options"), dict) else {}
            req = {**req, "stream_options": {**options, "include_usage": True}}
            body = json.dumps(req).encode()
        budgeted = self.budget is not None or job and job.get("budget") is not None
        owner, entry = self.litellm.find(asked)
        if model_call and budgeted and entry is None:
            return refuse(h, provider, 400,
                          f"The run has a budget, and the server has no price for model {asked!r}, so it can't "
                          "count the call: neither LiteLLM's live price table nor the one it came with lists it.")
        if owner and owner != provider and not self.subscription and owner not in self.litellm.keys:
            return refuse(h, provider, 503, f"The server holds no {owner} key, which {asked} needs: start the "
                          f"server with {owner.upper()}_API_KEY set.")
        # the model name LiteLLM serves; a subscription run's calls each stay with their own provider
        translated = (req is not None and route in FIELDS and not self.subscription
                      and self.litellm.translates(provider, owner, asked))
        if translated:  # to LiteLLM, which translates: only the fields the route defines, and no caller headers
            if not self.litellm.ensure():
                return refuse(h, provider, 503, f"The server's LiteLLM, which serves {asked}, isn't running.")
            kept = {k: v for k, v in req.items() if k in FIELDS[route]}
            body = json.dumps(uncached({**kept, "model": translated})).encode()
            up, path = urlsplit(self.litellm.url), route
            headers = {"content-type": "application/json", "accept-encoding": "identity",
                       "authorization": "Bearer " + self.litellm.master}
        else:
            held = (self.logins if self.subscription else self.litellm.keys).get(provider)
            if not held:
                return refuse(h, provider, 503,
                              f"The server holds no {provider} " + ("login." if self.subscription else "key."))
            headers = {k.lower(): v for k, v in h.headers.items() if k.lower() not in HOP}
            headers["accept-encoding"] = "identity"  # usage is read from the bytes as they pass
            path = rest
            if not self.subscription:
                base, key = held
                headers[KEY_HEADER[provider]] = f"Bearer {key}" if provider == "openai" else key
                # LiteLLM's Gemini upstream ends in /v1beta, and a Gemini agent's path starts with it
                base = base.removesuffix("/v1beta")
            else:  # as the harness calls with its login
                base, harness, login = held
                if (login.expires or float("inf")) < time.time():
                    return refuse(h, provider, 503, f"The server's {harness.name} login has expired. Start a "
                                  f"new run with a new {harness.login}: {harness.login_from}.")
                headers, path = login.sign(headers, rest)
            up = urlsplit(base)
        ticket = None
        if model_call and budgeted:
            ticket, why = self.reserve(job, entry, len(body))
            if why:
                return refuse(h, provider, 429, why)
        connection = http.client.HTTPConnection if up.scheme == "http" else http.client.HTTPSConnection
        conn = connection(up.hostname, up.port, timeout=900)
        status, seen, stream_err = None, bytearray(), None
        try:
            try:
                conn.request(h.command, up.path.rstrip("/") + "/" + path, body or None, headers)
                resp = conn.getresponse()
                status = resp.status
                h.send_response(status)
                for k, v in resp.getheaders():
                    if k.lower() not in HOP and not k.lower().startswith("x-litellm"):  # its upstreams and settings
                        h.send_header(k, v)
                caller = relay(h.end_headers)
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        if resp.length not in (None, 0):
                            stream_err = http.client.IncompleteRead(bytes(seen), resp.length)
                        break
                    seen += chunk
                    caller = caller and relay(lambda: (h.wfile.write(chunk), h.wfile.flush()))
            except (OSError, http.client.HTTPException) as e:
                stream_err = e
                h.log_message("upstream %s failed: %s", provider, e)
                if status is None:
                    return refuse(h, provider, 502, f"The server couldn't reach {provider}: {e}")
            finally:
                conn.close()
            # a job's calls are a trial's, not the member's conversation: they don't pause the member
            if model_call and not job and status in LIMITED:
                now = time.time()
                until = now + delay(resp, plain(bytes(seen), resp.getheader("Content-Encoding") or ""))
                with self.lock:
                    mine = self.limits.setdefault(who, {})
                    mine[asked] = (now, max(until, mine.get(asked, (0, 0))[1]))
            if not model_call or not 200 <= status < 300:
                return
            body = plain(bytes(seen), resp.getheader("Content-Encoding") or "")
            model, used = usage(provider, body)
            if not model and not any(used.values()) and stream_err is None:
                h.log_message("no model or usage in a %s reply (Content-Type %s, Content-Encoding %s), which begins %r",
                              provider, resp.getheader("Content-Type"), resp.getheader("Content-Encoding"), body[:120])
            if not asked:  # a request that named no model is priced as the model that answered
                entry = self.litellm.find(model)[1]
            if any(used[k] for k in USED):
                usd = price(entry, used)
            elif stream_err is not None and entry is not None:
                usd = 0.0  # priced model, but stream broke before usage arrived: don't report as unpriced
            else:
                usd = None  # no usage: unknown
            row = {"at": time.time(), "who": who, "model": model or asked, "route": f"{provider}/{route}", **used,
                   "usd": None if usd is None else round(usd, 8), **({"via": "litellm"} if translated else {}),
                   **({"job": job["id"]} if job else {}), **({"incomplete": True} if stream_err is not None else {})}
            with self.lock:
                if ticket is not None:
                    self.in_flight.pop(ticket, None)
                    ticket = None
                add(self.by, row)
                add(self.by_job, row, "job")
                if not job:  # the model's limit is over, and so is any other that has run out since
                    now = self.answered[who] = time.time()
                    mine = self.limits.get(who, {})
                    for m in [m for m, (_, until) in mine.items() if m == asked or until <= now]:
                        del mine[m]
                print(json.dumps(row), file=self.file, flush=True)
                os.fsync(self.file.fileno())  # budgets are counted from the ledger: a crash must not forget a call
        finally:
            if ticket is not None:
                with self.lock:
                    self.in_flight.pop(ticket, None)
