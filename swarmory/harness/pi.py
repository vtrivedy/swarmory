"""pi, Mario Zechner's coding agent: `pi --print --mode json`. Its model names its provider (anthropic/...,
openai/..., gemini/...), and it calls the gateway as that provider's API. pi reads no base URL from the environment,
so prepare writes the gateway's address to ~/.pi/agent/models.json, which names the <PROVIDER>_API_KEY variable that
holds the run token."""
import json
import os
from pathlib import Path

from .base import Endpoint, Login, cut, gateway, other, records, short, text_of

API = {"anthropic": ("anthropic-messages", "anthropic"),
       "openai": ("openai-responses", "openai/v1"),  # gpt-6 takes tools with reasoning only in the Responses API
       "gemini": ("google-generative-ai", "gemini/v1beta")}

EXTENSION = """import { execFileSync } from "node:child_process";
export default function (pi: any) {
  pi.on("tool_result", (event: any) => {
    let out = "";
    try { out = execFileSync("swarmory", ["inbox", "--hook"], { encoding: "utf8", timeout: 15000 }); } catch { return; }
    if (!out.trim()) return;
    return { content: [...event.content, { type: "text", text: JSON.parse(out).text }] };
  });
}
"""
QUIET = {"agent_start", "agent_end", "turn_start", "turn_end", "message_start", "message_update",
         "tool_execution_start", "tool_execution_update", "auto_retry_end", "compaction_end"}


class Pi:
    name = "pi"
    cli = "pi"
    install = ("npm install -g @mariozechner/pi-coding-agent@0.73.1",)
    provider = None
    login = None
    login_from = ""
    home = ".pi"
    conversations = "agent/sessions"
    hooks = True
    sandboxed = {}

    def env(self, llm: Endpoint) -> dict:
        return gateway(llm)

    def prepare(self, work: str, llm: Endpoint) -> None:
        """Points the model's provider at the gateway, with the key env() sets, and adds the extension that delivers
        messages between tool calls."""
        provider, _, model = os.environ["SWARMORY_MODEL"].partition("/")
        api, route = API[provider]
        agent = Path.home() / self.home / "agent"
        (agent / "extensions").mkdir(parents=True, exist_ok=True)
        (agent / "models.json").write_text(json.dumps({"providers": {provider: {
            "baseUrl": f"{llm.url}/{route}", "api": api, "apiKey": f"{provider.upper()}_API_KEY",
            "models": [{"id": model}]}}}) + "\n")
        (agent / "extensions" / "swarmory.ts").write_text(EXTENSION)

    def inbox(self, text: str) -> dict:
        return {"text": text}

    def command(self, prompt: str, resume: bool, model: str | None, args: list) -> list:
        return [self.cli, "--print", "--mode", "json", *(["--continue"] if resume else []),
                *(["--model", model] if model else []), *args, prompt]

    def signin(self, login: str) -> Login | None:
        return None

    def parse(self, lines):
        for e in records(lines):
            yield from event(e)


def event(e: dict) -> list:
    """One `pi --mode json` event into events. A message is shown once it ends, and a tool call once its message
    does; the streamed parts before are skipped."""
    t = e.get("type")
    if t == "session":
        return [{"kind": "init", "model": None, "tools": [], "mcp": []}]
    if t == "message_end":
        m = e.get("message") or {}
        if m.get("role") != "assistant":  # the prompt, which the session's start has, and tool results, shown below
            return []
        out = []
        for b in m.get("content") or []:
            kind = b.get("type") if isinstance(b, dict) else None
            if kind == "text" and b.get("text"):
                out.append({"kind": "say", "text": cut(b["text"])})
            elif kind == "thinking" and b.get("thinking"):
                out.append({"kind": "think", "text": cut(b.get("thinking") or ""), "tokens": None})
            elif kind == "toolCall":
                out.append({"kind": "tool", "id": b.get("id"), "name": b.get("name"),
                            "input": short(b.get("arguments") or {})})
        u = m.get("usage") or {}
        out.append({"kind": "usage", "input": u.get("input") or 0, "cached": u.get("cacheRead") or 0,
                    "written": u.get("cacheWrite") or 0, "output": u.get("output") or 0})
        if m.get("stopReason") in ("error", "aborted"):
            out.append({"kind": "error", "text": cut(m.get("errorMessage") or m["stopReason"])})
        return out
    if t == "tool_execution_end":
        return [{"kind": "result", "id": e.get("toolCallId"), "error": bool(e.get("isError")),
                 "text": cut(text_of((e.get("result") or {}).get("content")))}]
    if t == "auto_retry_start":
        return [{"kind": "warn", "text": cut(f"retry {e.get('attempt')} of {e.get('maxAttempts')}: "
                                             f"{e.get('errorMessage')}")}]
    if t == "compaction_start":
        return [{"kind": "compact", "tokens": None, "trigger": e.get("reason")}]
    return [] if t in QUIET else [other(e)]
