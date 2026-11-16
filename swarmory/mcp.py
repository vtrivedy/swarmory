"""A JSON-RPC server on stdin and stdout for MCP, for clients of two kinds. One of revision 2026-07-28, which is
stateless, says its protocol version and capabilities in each request's _meta. An older one says them once, in an
`initialize` handshake, and its later requests carry no _meta. tools() makes MCP tools from the server's verb table
(control_plane/verbs.py), so no verb is described twice. Standard library only.
"""
import json
import sys
import threading
import traceback

VERSION = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")  # the initialize-era revisions answered, newest first
META = "io.modelcontextprotocol/"
ITEMS = {"team": {"type": "object"}}  # what an array param holds when not strings, by the param's name


def schema(params: list) -> dict:
    """The JSON Schema of a verb's request, from its params as Verb.listing() gives them."""
    props = {p["name"]: {"type": p["type"], "description": p["doc"], **({"enum": p["enum"]} if p.get("enum") else {}),
                         **({"items": ITEMS.get(p["name"], {"type": "string"})} if p["type"] == "array" else {})}
             for p in params}
    need = [p["name"] for p in params if p["required"]]
    return {"type": "object", "properties": props, "additionalProperties": False,
            **({"required": need} if need else {})}


def tools(verbs: list) -> dict:
    """MCP tools by name, one per verb the operator may call, from the verbs' listings."""
    return {v["name"]: {"description": v["doc"], "inputSchema": schema(v["params"]), "annotations": v["hints"]}
            for v in verbs if "operator" in v["who"]}


class Fail(Exception):
    """A JSON-RPC error."""

    def __init__(self, code: int, message: str, data=None):
        self.code, self.message, self.data = code, message, data


class ToolError(Exception):
    """A failed call the model can act on: answered as a tool result with isError, not a protocol error."""


def serve(tools: dict, call, instructions: str):
    """Answers MCP on stdin and stdout until stdin closes. `tools` maps a name to a tool, as tools() makes them;
    call(name, arguments) returns the result's structuredContent or raises ToolError. Each request runs in its own
    thread, so a long call doesn't hold up the rest."""
    out, legacy = threading.Lock(), threading.Event()
    stdout = sys.stdout  # the client's, though a call may point sys.stdout elsewhere
    listed = [{"name": n, **t} for n, t in tools.items()]
    info = {"name": "swarmory", "version": "1"}

    def answer(msg):
        method, params = msg.get("method"), msg.get("params") or {}
        meta = params.get("_meta") or {}
        if method == "initialize":
            legacy.set()  # the client speaks an older revision, for the rest of this process
            asked = params.get("protocolVersion")
            return {"protocolVersion": asked if asked in LEGACY else LEGACY[0], "capabilities": {"tools": {}},
                    "serverInfo": info, "instructions": instructions}
        if method == "ping":
            return {}
        version = meta.get(META + "protocolVersion")
        if version is not None or not legacy.is_set():  # after initialize, requests carry no _meta
            if version is None or not isinstance(meta.get(META + "clientCapabilities"), dict):
                raise Fail(-32602, f"_meta needs {META}protocolVersion and {META}clientCapabilities")
            if version != VERSION:
                raise Fail(-32022, "Unsupported protocol version", {"supported": [VERSION], "requested": version})
        done = {"resultType": "complete", "_meta": {META + "serverInfo": info}}
        if method == "server/discover":
            return {**done, "supportedVersions": [VERSION], "capabilities": {"tools": {}},
                    "instructions": instructions, "ttlMs": 3600_000, "cacheScope": "public"}
        if method == "tools/list":
            return {**done, "tools": listed, "ttlMs": 3600_000, "cacheScope": "public"}
        if method != "tools/call":
            raise Fail(-32601, f"no method {method}")
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in tools:
            raise Fail(-32602, f"no tool {name}")
        if not isinstance(args, dict):
            raise Fail(-32602, "arguments must be an object")
        try:
            result, failed = call(name, args), False
        except ToolError as e:
            result, failed = str(e), True
        body = {"content": [{"type": "text", "text": json.dumps(result)}], "isError": failed}
        return {**done, **body, **({} if failed else {"structuredContent": result})}

    def respond(msg):
        try:
            reply = {"result": answer(msg)}
        except Fail as e:
            reply = {"error": {"code": e.code, "message": e.message, **({"data": e.data} if e.data else {})}}
        except Exception as e:
            traceback.print_exc()
            reply = {"error": {"code": -32603, "message": f"swarmory failed: {type(e).__name__}"}}
        write({"jsonrpc": "2.0", "id": msg["id"], **reply})

    def write(msg):
        with out:
            stdout.write(json.dumps(msg) + "\n")
            stdout.flush()

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}})
            continue
        if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
            write({"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                   "error": {"code": -32600, "message": "want one JSON-RPC request per line"}})
        elif "id" in msg:  # a notification (notifications/initialized, notifications/cancelled) needs nothing
            threading.Thread(target=respond, args=(msg,)).start()  # at EOF, the process exits once these end
