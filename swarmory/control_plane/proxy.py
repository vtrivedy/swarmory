"""A run's gateway, inside the server: every model call a member makes comes here with the member's run token, never a
key. The gateway forwards the call upstream with the real key, reads the tokens it used from the response (streamed or
not), prices them from LiteLLM's price table (litellm.py), and writes them to the run's ledger.jsonl. It forwards only
the routes in ROUTES. A model of another provider than the route's, named <provider>/<model> or by a bare name the
price table lists under that provider, goes to LiteLLM, which translates the call. Each call is refused once the run
or its job has spent its budget.

litellm.py names the variables with the providers' API keys and upstreams. A run with subscriptions calls with the
person's logins instead, the way each harness calls when it holds its login itself: each harness's file in
swarmory/harness/ names the variable with its login and says how a logged-in call is signed and where it goes. A key
or login that isn't set turns that API off.
"""
import http.client
import json
import os
import threading
import time
import zlib
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
}
ROUTES = {  # (provider, method, path) -> whether it is a model call, recorded in the ledger
    ("anthropic", "POST", "v1/messages"): True,
    ("anthropic", "POST", "v1/messages/count_tokens"): False,
    ("anthropic", "GET", "v1/models"): False,
    ("openai", "POST", "v1/responses"): True,
    ("openai", "GET", "v1/models"): False,
}
HOP = {"host", "content-length", "connection", "transfer-encoding", "accept-encoding", "authorization", "x-api-key",
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
    says: input (not from cache), cached, written (to cache) and output. OpenAI's input count includes the cached
    part; Anthropic's doesn't."""
    try:
        events = [json.loads(body)]
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
        model = r.get("model") or model
        u = r.get("usage") or e.get("usage") or {}
        if provider == "anthropic":
            split = u.get("cache_creation")
            if isinstance(split, dict) and split.get("ephemeral_1h_input_tokens"):  # writes to the hour-long cache
                used["written_1h"] = max(used.get("written_1h", 0), split["ephemeral_1h_input_tokens"])
            for key, field in (("input", "input_tokens"), ("cached", "cache_read_input_tokens"),
                               ("written", "cache_creation_input_tokens"), ("output", "output_tokens")):
                used[key] = max(used[key], u.get(field) or 0)
        elif u:
            cached = (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
            used.update(input=(u.get("input_tokens") or 0) - cached, cached=cached, output=u.get("output_tokens") or 0)
    return model, used


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
        for h in reversed(HARNESSES) if subscription else ():
            if login := env.get(h.login) and h.signin(env[h.login]):
                self.logins[h.provider] = (upstream(env, h.provider, login.upstream), h, login)
        self.lock = threading.Lock()
        self.by, self.by_job = {}, {}  # a restarted server carries on from what it recorded
        replay(self.ledger, lambda row: (add(self.by, row), add(self.by_job, row, "job")))
        self.file = open(self.ledger, "a")

    def totals(self, jobs: bool = False) -> dict:
        """Calls, tokens and dollars by member, or by job."""
        with self.lock:
            return {k: dict(v) for k, v in (self.by_job if jobs else self.by).items()}

    def over(self, job) -> str | None:
        """Why a call now would be refused: the run or the job has spent its budget, or the job its tokens."""
        with self.lock:
            run = sum(s["usd"] for s in self.by.values())
            used = dict(self.by_job.get(job["id"], {})) if job else {}
        if self.budget is not None and run >= self.budget:
            return f"The run has spent {money(run)} of its {money(self.budget)} budget."
        if job and job.get("tokens") and sum(used.get(k, 0) for k in USED) >= job["tokens"]:
            return f"Job {job['id']} has used its {job['tokens']:,} tokens."
        if job and job.get("budget") is not None and used.get("usd", 0) >= job["budget"]:
            return f"Job {job['id']} has spent {money(used['usd'])} of its {money(job['budget'])} budget."
        return None

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
        model_call = ROUTES.get((provider, h.command, route))
        if model_call is None:
            return refuse(h, provider, 404, f"The server doesn't forward {h.command} {route}: it forwards only "
                          f"{', '.join(m + ' ' + r for p, m, r in ROUTES if p == provider)}.")
        try:
            req = json.loads(body) if body else None
        except (ValueError, RecursionError):
            req = None
        req = req if isinstance(req, dict) else None
        asked = req.get("model") if req and isinstance(req.get("model"), str) else ""
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
        translated = req is not None and not self.subscription and self.litellm.translates(provider, owner, asked)
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
                headers.update({"x-api-key": key} if provider == "anthropic" else {"authorization": f"Bearer {key}"})
            else:  # as the harness calls with its login
                base, harness, login = held
                if (login.expires or float("inf")) < time.time():
                    return refuse(h, provider, 503, f"The server's {harness.name} login has expired. Start a "
                                  f"new run with a new {harness.login}: {harness.login_from}.")
                headers, path = login.sign(headers, rest)
            up = urlsplit(base)
        connection = http.client.HTTPConnection if up.scheme == "http" else http.client.HTTPSConnection
        conn = connection(up.hostname, up.port, timeout=900)
        status, seen = None, bytearray()
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
                    break
                seen += chunk
                caller = caller and relay(lambda: (h.wfile.write(chunk), h.wfile.flush()))
        except (OSError, http.client.HTTPException) as e:
            h.log_message("upstream %s failed: %s", provider, e)
            if status is None:
                return refuse(h, provider, 502, f"The server couldn't reach {provider}: {e}")
        finally:
            conn.close()
        if not model_call or not 200 <= status < 300:
            return
        body = plain(bytes(seen), resp.getheader("Content-Encoding") or "")
        model, used = usage(provider, body)
        if not model and not any(used.values()):
            h.log_message("no model or usage in a %s reply (Content-Type %s, Content-Encoding %s), which begins %r",
                          provider, resp.getheader("Content-Type"), resp.getheader("Content-Encoding"), body[:120])
        if not asked:  # a request that named no model is priced as the model that answered
            entry = self.litellm.find(model)[1]
        usd = price(entry, used) if any(used[k] for k in USED) else None  # no usage: unknown
        row = {"at": time.time(), "who": who, "model": model or asked, "route": f"{provider}/{route}", **used,
               "usd": None if usd is None else round(usd, 8), **({"via": "litellm"} if translated else {}),
               **({"job": job["id"]} if job else {})}
        with self.lock:
            add(self.by, row)
            add(self.by_job, row, "job")
            print(json.dumps(row), file=self.file, flush=True)
            os.fsync(self.file.fileno())  # budgets are counted from the ledger: a crash must not forget a call
