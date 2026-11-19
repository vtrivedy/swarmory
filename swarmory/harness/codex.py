"""Codex: `codex exec`, which speaks OpenAI's Responses API and prints `--json` events."""
import base64
import json
from pathlib import Path

from .base import INBOX, Endpoint, Login, cut, other, records, short, text_of


class Codex:
    name = "codex"
    cli = "codex"
    install = ("npm install -g @openai/codex@0.166.0",)
    provider = "openai"
    login = "CODEX_AUTH_JSON"
    login_from = "Codex's ~/.codex/auth.json from a ChatGPT login, which any codex command refreshes"
    home = ".codex"
    conversations = "sessions"
    sandboxed = {}

    def env(self, llm: Endpoint) -> dict:
        return {"SWARMORY_TOKEN": llm.token}  # the variable prepare's config names as the key

    def prepare(self, work: str, llm: Endpoint) -> None:
        """Points it at the gateway, which it takes only from its config file, and adds the hook that delivers
        messages between tool calls."""
        config = Path.home() / self.home / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('model_provider = "swarmory"\n[model_providers.swarmory]\nname = "swarmory"\n'
                          f'base_url = "{llm.url}/openai/v1"\nenv_key = "SWARMORY_TOKEN"\nwire_api = "responses"\n'
                          "supports_websockets = false\n")
        (config.parent / "hooks.json").write_text(json.dumps({"hooks": INBOX}) + "\n")

    def command(self, prompt: str, resume: bool, model: str | None, args: list) -> list:
        return [self.cli, "exec", *(["resume", "--last"] if resume else []), "--json", "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
                "--dangerously-bypass-hook-trust",  # the hook is ours: it runs without Codex's interactive review
                *(["-m", model] if model else []), *args, prompt]

    def once(self, prompt: str, model: str | None, folder: str) -> list:
        return [self.cli, "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
                "-c", "project_doc_max_bytes=0",  # no AGENTS.md from the folders above
                *(["-m", model] if model else []), prompt]

    def signin(self, login: str) -> Login | None:
        """A ChatGPT login from auth.json: its calls go to ChatGPT, whose routes have no v1/."""
        try:
            tokens = json.loads(login)["tokens"]
            claims = tokens["access_token"].split(".")[1]
            expires = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))["exp"]
            signed = {"authorization": f"Bearer {tokens['access_token']}", "chatgpt-account-id": tokens["account_id"]}
        except (ValueError, TypeError, KeyError, IndexError):
            return None
        return Login(lambda headers, path: ({**headers, **signed}, path.removeprefix("v1/")), expires,
                     "https://chatgpt.com/backend-api/codex")

    def parse(self, lines):
        seen = set()  # the ids of command items already shown started
        for e in records(lines):
            out = []
            event(e, out, seen)
            yield from out


def event(e: dict, out: list, seen: set):
    """One Codex `exec --json` event into events. `seen` holds the ids of command items already shown started."""
    t = e.get("type")
    item = e.get("item") if isinstance(e.get("item"), dict) else None
    if t == "thread.started":
        out.append({"kind": "init", "model": None, "tools": [], "mcp": []})
    elif t == "turn.started":
        pass
    elif t == "turn.completed":
        u = e.get("usage") or {}
        cached = u.get("cached_input_tokens") or 0
        out.append({"kind": "usage", "input": (u.get("input_tokens") or 0) - cached, "cached": cached, "written": 0,
                    "output": u.get("output_tokens") or 0})
    elif t == "turn.failed":
        out.append({"kind": "error", "text": cut((e.get("error") or {}).get("message") or json.dumps(e))})
    elif t == "error":  # a stream that dropped and reconnects: the turn carries on, or a turn.failed follows
        out.append({"kind": "warn", "text": cut(e.get("message") or json.dumps(e))})
    elif item and t in ("item.started", "item.updated", "item.completed"):
        kind, iid = item.get("type"), item.get("id")
        if kind == "command_execution":
            if iid not in seen:
                seen.add(iid)
                out.append({"kind": "tool", "id": iid, "name": "shell",
                            "input": {"command": cut(item.get("command") or "")}})
            if t == "item.completed":
                code = item.get("exit_code")
                out.append({"kind": "result", "id": iid, "text": cut(item.get("aggregated_output") or ""),
                            "error": code not in (0, None) or item.get("status") == "failed", "exit": code})
        elif t != "item.completed":
            pass  # the rest are shown once, complete
        elif kind == "agent_message":
            out.append({"kind": "say", "text": cut(item.get("text") or "")})
        elif kind == "reasoning":
            out.append({"kind": "think", "text": cut(item.get("text") or ""), "tokens": None})
        elif kind == "file_change":
            changes = [{"path": c.get("path"), "kind": c.get("kind")} for c in item.get("changes") or []
                       if isinstance(c, dict)]
            out.append({"kind": "tool", "id": iid, "name": "edit", "input": {"changes": changes}})
            out.append({"kind": "result", "id": iid, "text": item.get("status") or "", "error":
                        item.get("status") == "failed"})
        elif kind == "mcp_tool_call":
            name = f"{item.get('server')}.{item.get('tool')}"
            out.append({"kind": "tool", "id": iid, "name": name, "input": short(item.get("arguments") or {})})
            err = item.get("error")
            out.append({"kind": "result", "id": iid, "error": bool(err) or item.get("status") == "failed",
                        "text": cut(text_of((err or {}).get("message") if isinstance(err, dict) else err)
                                    if err else text_of((item.get("result") or {}).get("content")))})
        elif kind == "web_search":
            out.append({"kind": "tool", "id": iid, "name": "web_search", "input": {"query": item.get("query")}})
        elif kind == "todo_list":
            out.append({"kind": "todo", "items": [{"text": i.get("text"), "done": bool(i.get("completed"))}
                                                  for i in item.get("items") or [] if isinstance(i, dict)]})
        elif kind == "error":  # Codex's non-fatal errors, such as a model it has no metadata for
            out.append({"kind": "warn", "text": cut(item.get("message") or "")})
        else:
            out.append(other(item))
    else:
        out.append(other(e))
