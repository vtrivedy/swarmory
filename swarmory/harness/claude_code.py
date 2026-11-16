"""Claude Code: `claude -p`, which speaks Anthropic's API and prints stream-json."""
import json
import os
from pathlib import Path

from .base import Endpoint, Login, cut, other, records, short, text_of

DOCS = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md", ".claude/rules/**")  # instructions it reads on its own


class ClaudeCode:
    name = "claude-code"
    cli = "claude"
    install = ("npm install -g @anthropic-ai/claude-code@2.1.312",)
    provider = "anthropic"
    login = "CLAUDE_CODE_OAUTH_TOKEN"
    login_from = "the token `claude setup-token` prints"
    home = ".claude"
    conversations = "projects"
    sandboxed = {"IS_SANDBOX": "1",  # lets it skip its permission prompts as root
                 "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
                 "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}

    def env(self, llm: Endpoint) -> dict:
        return {"ANTHROPIC_BASE_URL": f"{llm.url}/anthropic", "ANTHROPIC_API_KEY": llm.token}

    def prepare(self, work: str, llm: Endpoint) -> None:
        """Excludes the instruction files in every folder above `work`, which it would read: locally, swarmory's own."""
        above = Path(os.path.realpath(work)).parents  # real: as the session sees its cwd
        settings = Path.home() / self.home / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"claudeMdExcludes": [str(d / f) for d in above for f in DOCS]}) + "\n")

    def command(self, prompt: str, resume: bool, model: str | None, args: list) -> list:
        return [self.cli, "-p", *(["--continue"] if resume else []), prompt, "--output-format", "stream-json",
                "--verbose", "--dangerously-skip-permissions", *(["--model", model] if model else []), *args]

    def once(self, prompt: str, model: str | None, folder: str) -> list:
        real, mine = Path(os.path.realpath(folder)), str(Path.home() / self.home) + os.sep  # the person's own stay
        docs = [str(d / f) for d in [real, *real.parents] for f in DOCS]
        return [self.cli, "-p", prompt, "--dangerously-skip-permissions",
                "--settings", json.dumps({"claudeMdExcludes": [d for d in docs if not d.startswith(mine)]}),
                *(["--model", model] if model else [])]

    def signin(self, login: str) -> Login:
        def sign(headers: dict, path: str) -> tuple:
            beta = headers.get("anthropic-beta")
            return {**headers, "authorization": f"Bearer {login}",
                    "anthropic-beta": f"{beta},oauth-2025-04-20" if beta else "oauth-2025-04-20"}, path
        return Login(sign)

    def parse(self, lines):
        last = {}  # the running estimate thinking_tokens events give ahead of the thinking block they size
        for e in records(lines):
            out = []
            event(e, out, last)
            yield from out


def event(e: dict, out: list, last: dict):
    """One Claude Code stream-json event into events. `last` keeps the running estimate thinking_tokens events give
    ahead of the thinking block they size, so an event never changes once made."""
    t, sub = e.get("type"), e.get("subtype")
    parent = e.get("parent_tool_use_id")
    if t == "system" and sub == "init":
        out.append({"kind": "init", "model": e.get("model"), "tools": e.get("tools") or [],
                    "mcp": [s.get("name") for s in e.get("mcp_servers") or [] if isinstance(s, dict)]})
    elif t == "system" and sub == "thinking_tokens":
        last["tokens"] = e.get("estimated_tokens")
    elif t == "system" and sub == "compact_boundary":
        meta = e.get("compact_metadata") or {}
        out.append({"kind": "compact", "tokens": meta.get("pre_tokens"), "trigger": meta.get("trigger")})
    elif t == "system" and sub in ("task_started", "task_updated", "task_notification", "task_progress"):
        status = (e.get("patch") or {}).get("status") or e.get("status") or ("started" if sub == "task_started" else "")
        out.append({"kind": "task", "id": e.get("task_id"), "status": status,
                    "text": cut(e.get("summary") or e.get("description") or "", 400)})
    elif t == "system" and sub == "vcs_state_changed":
        out.append({"kind": "note", "text": f"git {e.get('kind') or 'changed'}"})
    elif t == "system" and sub in ("background_tasks_changed", "status", "hook_started", "hook_response"):
        pass  # a list of tasks the task events already gave, or harness bookkeeping
    elif t == "assistant":
        for b in (e.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                out.append({"kind": "say", "text": cut(b["text"]), "parent": parent})
            elif b.get("type") in ("thinking", "redacted_thinking"):
                out.append({"kind": "think", "text": cut(b.get("thinking") or ""), "tokens": last.pop("tokens", None),
                            "parent": parent})
            elif b.get("type") in ("tool_use", "server_tool_use"):
                out.append({"kind": "tool", "id": b.get("id"), "name": b.get("name"),
                            "input": short(b.get("input") or {}), "parent": parent})
    elif t == "user":
        content = (e.get("message") or {}).get("content")
        for b in content if isinstance(content, list) else [{"type": "text", "text": content}]:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                out.append({"kind": "result", "id": b.get("tool_use_id"), "text": cut(text_of(b.get("content"))),
                            "error": bool(b.get("is_error")), "parent": parent})
            elif isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip():
                out.append({"kind": "user", "text": cut(b["text"]), "parent": parent})
    elif t == "result":
        u = e.get("usage") or {}
        out.append({"kind": "usage", "input": u.get("input_tokens") or 0,
                    "cached": u.get("cache_read_input_tokens") or 0,
                    "written": u.get("cache_creation_input_tokens") or 0, "output": u.get("output_tokens") or 0,
                    "duration_ms": e.get("duration_ms"), "turns": e.get("num_turns"),
                    "error": bool(e.get("is_error")) or sub not in (None, "success")})
        if e.get("is_error") and e.get("result"):
            out.append({"kind": "error", "text": cut(str(e["result"]))})
    elif t in ("tool_progress", "stream_event"):
        pass  # heartbeats of a running tool, and the deltas of a message that arrives whole anyway
    else:
        out.append(other(e))
