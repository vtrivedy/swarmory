"""The task's MCP server, over stdio: newline-delimited JSON-RPC on stdin and stdout. Its tools are the environment's
own: get_word tells the word the verifier expects, and write_file writes a file in the container."""
import json
import sys

WORD = "lighthouse"
TOOLS = [
    {"name": "get_word", "description": "The secret word this environment holds.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "write_file", "description": "Write text to a file in the environment.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "text": {"type": "string"}},
                     "required": ["path", "text"]}},
]


def call(name: str, args: dict) -> str:
    if name == "get_word":
        return WORD
    if name == "write_file":
        with open(args["path"], "w") as f:
            f.write(args["text"])
        return f"wrote {args['path']}"
    raise ValueError(f"there is no tool {name}")


for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:  # a notification
        continue
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": "env", "version": "1.0.0"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            result = {"content": [{"type": "text", "text": call(params["name"], params.get("arguments") or {})}]}
        except Exception as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                          "error": {"code": -32601, "message": f"no method {method}"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
