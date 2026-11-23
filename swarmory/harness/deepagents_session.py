"""One headless Deep Agents session, run by the deepagents harness (deep_agents.py) with the SDK in uv's environment:

    python deepagents_session.py PROVIDER/MODEL PROMPT [--resume]

The agent works in the current folder with the SDK's file tools and an unrestricted shell. Its conversation is a
SQLite checkpoint in ~/.deepagents/conversations, so --resume carries on where the last session stopped. Before each
model call it runs `swarmory inbox --hook` and gives the model what came for it. It prints one line of JSON per event,
in the shape swarmory's traces use (control_plane/traces.py)."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain.agents.middleware import before_model
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver

PROVIDERS = {"anthropic": ("anthropic", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"),
             "openai": ("openai", "OPENAI_BASE_URL", "OPENAI_API_KEY"),
             "gemini": ("google_genai", "GOOGLE_GEMINI_BASE_URL", "GEMINI_API_KEY")}
HOME_DIR = Path.home() / ".deepagents"
CONVERSATIONS = HOME_DIR / "conversations"
DB = HOME_DIR / "session.db"


def emit(**event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def model(name: str):
    provider, _, rest = name.partition("/")
    lc, url, key = PROVIDERS[provider]
    return init_chat_model(f"{lc}:{rest}", base_url=os.environ[url], api_key=os.environ[key])


@before_model
def inbox(state, runtime):
    """Messages that came during the turn, as a user message before the next model call."""
    try:
        out = subprocess.run(["swarmory", "inbox", "--hook"], capture_output=True, text=True, timeout=15).stdout
        text = json.loads(out)["text"] if out.strip() else ""
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return None
    return {"messages": [HumanMessage(text)]} if text else None


def events(update: dict):
    """A step's new messages as events."""
    for part in (update or {}).values():
        messages = part.get("messages") if isinstance(part, dict) else None
        for m in messages if isinstance(messages, list) else ():
            if isinstance(m, AIMessage):
                if m.text:
                    emit(kind="say", text=m.text)
                for c in m.tool_calls:
                    emit(kind="tool", id=c["id"], name=c["name"], input=c["args"])
                u = m.usage_metadata or {}
                if u:
                    d = u.get("input_token_details") or {}
                    # Anthropic's writes split by cache lifetime are in the ephemeral keys, with cache_creation 0
                    cached = d.get("cache_read") or 0
                    written = sum(d.get(k) or 0 for k in ("cache_creation", "ephemeral_5m_input_tokens",
                                                          "ephemeral_1h_input_tokens"))
                    emit(kind="usage", input=u.get("input_tokens", 0) - cached - written, cached=cached,
                         written=written, output=u.get("output_tokens", 0))
            elif isinstance(m, ToolMessage):
                emit(kind="result", id=m.tool_call_id, error=m.status == "error", text=m.text)


def main(name: str, prompt: str, flag: str = ""):
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    if flag != "--resume":
        for suffix in ("", "-wal", "-shm"):
            Path(f"{DB}{suffix}").unlink(missing_ok=True)
    conn = sqlite3.connect(DB, check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        config = {"configurable": {"thread_id": "main"}, "recursion_limit": 100_000}
        if flag == "--resume" and saver.get_tuple(config) is None:
            sys.exit(f"There is no conversation to resume in {CONVERSATIONS}.")
        backend = LocalShellBackend(root_dir=os.getcwd(), virtual_mode=False, inherit_env=True, timeout=1800)
        agent = create_deep_agent(model(name), backend=backend, checkpointer=saver, middleware=[inbox])
        emit(kind="init", model=name, tools=[], mcp=[])
        marked = False
        try:
            for update in agent.stream({"messages": [HumanMessage(prompt)]}, config, stream_mode="updates"):
                if not marked:
                    CONVERSATIONS.mkdir(parents=True, exist_ok=True)
                    (CONVERSATIONS / "checkpoint").touch()
                    marked = True
                events(update)
        finally:
            if not marked and saver.get_tuple(config) is not None:
                CONVERSATIONS.mkdir(parents=True, exist_ok=True)
                (CONVERSATIONS / "checkpoint").touch()
    finally:
        conn.close()


if __name__ == "__main__":
    main(*sys.argv[1:])
