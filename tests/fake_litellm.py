#!/usr/bin/env python3
"""A stand-in for the litellm executable, for tests/test_proxy.py and tests/test_server.py: run as
swarmory/control_plane/litellm.py runs LiteLLM, it answers health checks and translated calls. It writes what it was
given (its environment's names, and each request) to seen.jsonl in its folder."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def note(**what):
    with open("seen.jsonl", "a") as f:
        print(json.dumps(what), file=f)


class Fake(BaseHTTPRequestHandler):
    def reply(self, status, body, stream=False):
        data = body if stream else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply(200, {"status": "ok"})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        note(path=self.path, headers={k.lower(): v for k, v in self.headers.items()}, body=req)
        usage = {"input_tokens": 30, "output_tokens": 7}
        if self.path == "/v1/responses":
            return self.reply(200, {"model": req["model"], "usage": usage})
        events = [{"type": "message_start", "message": {"model": req["model"], "usage": usage}},
                  {"type": "message_delta", "usage": usage}]
        self.reply(200, b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events), stream=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1])
    note(argv=args, env=sorted(os.environ), config=json.load(open(args[args.index("--config") + 1])))
    ThreadingHTTPServer(("127.0.0.1", port), Fake).serve_forever()
