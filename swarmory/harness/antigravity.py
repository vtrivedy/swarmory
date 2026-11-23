"""Google Antigravity's CLI: `agy -p`, which speaks the Gemini API with a key and prints stream-json. It runs only
Gemini models: the ones `agy models` lists, named without a provider, such as gemini-3.8-flash-low."""
import json
from pathlib import Path

from .base import Endpoint, Login, cut, other, records, short


class Antigravity:
    name = "antigravity"
    cli = "agy"
    install = ("curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- --dir /usr/local/bin",)
    provider = "gemini"
    login = None  # its Google sign-in calls Google's own service, which the gateway doesn't serve
    login_from = ""
    home = ".gemini"
    conversations = "antigravity-cli/conversations"
    hooks = True
    sandboxed = {}

    def env(self, llm: Endpoint) -> dict:
        return {"GOOGLE_GEMINI_BASE_URL": f"{llm.url}/gemini", "GEMINI_API_KEY": llm.token,
                "AGY_CLI_DISABLE_AUTO_UPDATE": "1"}

    def prepare(self, work: str, llm: Endpoint) -> None:
        """Has it use the key, not a Google sign-in, and adds the hook that delivers messages before each model
        call."""
        home = Path.home() / self.home
        for path, value in (("antigravity-cli/settings.json", {"modelProvider": "gemini"}),
                            ("config/hooks.json", {"swarmory": {"PreInvocation": [
                                {"type": "command", "command": "swarmory inbox --hook", "timeout": 15}]}})):
            (home / path).parent.mkdir(parents=True, exist_ok=True)
            (home / path).write_text(json.dumps(value) + "\n")

    def inbox(self, text: str) -> dict:
        return {"injectSteps": [{"systemMessage": {"systemMessage": text}}]}

    def command(self, prompt: str, resume: bool, model: str | None, args: list) -> list:
        return [self.cli, "-p", prompt, *(["--continue"] if resume else []), "--output-format", "stream-json",
                "--dangerously-skip-permissions", *(["--model", model] if model else []), *args]

    def signin(self, login: str) -> Login | None:
        return None

    def parse(self, lines):
        said = {}
        for e in records(lines):
            out = []
            event(e, out, said)
            yield from out


def event(e: dict, out: list, said: dict):
    """One `agy -p --output-format stream-json` event into events. A reply comes as text deltas, shown whole when its
    step is done."""
    t = e.get("event")
    s = e.get("step_update") if isinstance(e.get("step_update"), dict) else {}
    if t == "init":
        init = e.get("init") or {}
        out.append({"kind": "init", "model": init.get("model"), "tools": init.get("tools") or [], "mcp": []})
    elif t == "result":
        r = e.get("result") or {}
        u = r.get("usage") or {}
        cached = u.get("cache_read_tokens") or 0
        out.append({"kind": "usage", "input": (u.get("input_tokens") or 0) - cached, "cached": cached, "written": 0,
                    "output": (u.get("output_tokens") or 0) + (u.get("thinking_tokens") or 0)})
        if r.get("error"):
            out.append({"kind": "error", "text": cut(r["error"])})
    elif t == "step_update":
        kind, i, done = s.get("step_type"), s.get("step_index"), s.get("state") == "DONE"
        if kind == "agent_response":
            said[i] = said.get(i, "") + (s.get("text_delta") or "")
            if done and said.get(i):
                out.append({"kind": "say", "text": cut(said.pop(i))})
        elif kind == "tool":
            tool = s.get("tool_info") or {}
            if not done:
                out.append({"kind": "tool", "id": str(i), "name": tool.get("name") or s.get("tool_name"),
                            "input": short(tool.get("parameters") or {})})
            else:
                err = tool.get("error")
                out.append({"kind": "result", "id": str(i), "error": bool(err),
                            "text": cut((err or {}).get("message") if isinstance(err, dict) else
                                        tool.get("output") or "")})
        elif kind in ("user_input", "checkpoint"):
            pass
        elif kind == "system_message":  # what the hook injected; the stream gives no text
            out.append({"kind": "note", "text": "a message was given mid-turn"})
        elif kind == "error_message":
            out.append({"kind": "error", "text": cut(json.dumps(s))})
        else:
            out.append(other({"type": f"step/{kind}", **s}))
    else:
        out.append(other(e))
