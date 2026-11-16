"""The server's LiteLLM, which every run's gateway (proxy.py) shares, in two parts. Its price table, a JSON file that
comes with the litellm executable, is read here once: every model call the server makes is priced from it in this
process, by LiteLLM's rules, and it names each model's provider. LiteLLM the process, the server's child on 127.0.0.1,
serves only the calls that need translating: those to a model of another provider than the route's, such as
openai/gpt-6.1-sol or xai/grok-4.3 on the anthropic route (a coding agent inside a job's trial can drop the provider
from the name, so a model's provider is its prefix or, without one, the provider the price table lists for it). It
starts on the first such call. Native calls and subscription logins never reach it.

It runs SWARMORY_PROXY_LITELLM (the litellm executable from litellm.txt, pinned by hash), with no database and no
callbacks. Its environment holds the providers' API keys and base URLs and nothing else, so it never sees a login or a
run token.

    SWARMORY_PROXY_LITELLM         the litellm executable; unset, calls are neither priced nor translated
    SWARMORY_PROXY_ANTHROPIC       where calls to Anthropic's API go  (default https://api.anthropic.com)
    SWARMORY_PROXY_OPENAI          where calls to OpenAI's API go     (default https://api.openai.com)
    SWARMORY_PROXY_<PROVIDER>      LiteLLM's api_base for another provider, as LiteLLM takes it (default: its own)

The keys are ANTHROPIC_API_KEY, OPENAI_API_KEY and, for calls LiteLLM translates, <PROVIDER>_API_KEY for any provider
the price table names, spelled as LiteLLM spells it: GEMINI_API_KEY, XAI_API_KEY, MISTRAL_API_KEY.
"""
import http.client
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

PROVIDERS = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com",
             "gemini": "https://generativelanguage.googleapis.com/v1beta"}  # providers a plain model name may be of
STARTUP = 120  # seconds LiteLLM may take to answer after it starts
TIER = re.compile(r"^input_cost_per_token_above_(\d+)k_tokens$")  # a price entry's long-context tiers
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def upstream(env, provider: str, default: str = None) -> str | None:
    """Where calls to `provider`'s API go, without the /v1 of its routes: SWARMORY_PROXY_<PROVIDER> (Gemini has none),
    else `default`, else the provider's own API. For a provider that is not one of PROVIDERS it is what LiteLLM
    takes as that provider's api_base, as given, or None for LiteLLM's own."""
    base = (provider != "gemini" and env.get(f"SWARMORY_PROXY_{provider.upper()}") or default
            or PROVIDERS.get(provider))
    return base and base.rstrip("/")


def install() -> str:
    """The litellm executable a local server runs, in an environment of its own under ~/.swarmory/cache that is
    installed once from litellm.txt, every package checked against its hash."""
    lock = Path(__file__).with_name("litellm.txt")
    version = re.search(r"^litellm==(\S+)", lock.read_text(), re.M)[1]
    venv = Path.home() / ".swarmory" / "cache" / f"litellm-{version}"
    done = venv / "installed-from-lock"  # written last, so a cut-short install is redone
    venv.parent.mkdir(parents=True, exist_ok=True)
    if not done.is_file() or done.read_text() != lock.read_text():
        print(f"Installing LiteLLM {version} into {venv}, checking each package's hash. This happens once.",
              file=sys.stderr, flush=True)
        shutil.rmtree(venv, ignore_errors=True)
        for cmd in (["uv", "venv", "-q", "--python", "3.12", str(venv)],
                    ["uv", "pip", "install", "-q", "--python", str(venv / "bin" / "python"), "--require-hashes",
                     "-r", str(lock)]):
            if subprocess.run(cmd).returncode:
                sys.exit(f"Could not install LiteLLM {version}: `{' '.join(cmd)}` failed.")
        done.write_text(lock.read_text())
    return str(venv / "bin" / "litellm")


class LiteLLM:
    def __init__(self, data: Path, env):
        self.exe, self.dir = env.get("SWARMORY_PROXY_LITELLM"), Path(data)
        self.master, self.url, self.proc, self.retry = "sk-" + secrets.token_hex(24), None, None, 0.0
        self.lock = threading.Lock()
        self.table = {}  # model: its prices and provider, from the price table that comes with the litellm executable
        table = self.exe and next(Path(self.exe).resolve().parent.parent.glob(
            "lib/python*/site-packages/litellm/model_prices_and_context_window_backup.json"), None)
        if table:
            self.table = {m: e for m, e in json.loads(table.read_text()).items() if isinstance(e, dict)}
        elif self.exe:
            print(f"swarmory: no price table beside {self.exe}, so no model call is priced", file=sys.stderr,
                  flush=True)
        # the prefix of every priced model named `provider/model`, when the row says that is its provider: the names
        # LiteLLM serves by prefix, without the table's other labels and its services that have no token price
        self.known = set(PROVIDERS) | {m.partition("/")[0] for m, e in self.table.items()
                                       if e.get("input_cost_per_token") is not None
                                       and m.partition("/")[0] == e.get("litellm_provider") and m.isascii()
                                       and m.partition("/")[0].isidentifier()}
        # provider: (upstream, key), for each of them whose API key the server holds
        self.keys = {p: (upstream(env, p), env[f"{p.upper()}_API_KEY"]) for p in sorted(self.known)
                     if env.get(f"{p.upper()}_API_KEY")}
        if self.exe:
            self.end_last()  # now, not at the first translated call: it holds the providers' keys

    def ensure(self) -> bool:
        """Starts LiteLLM if it isn't running, and waits until it answers. False if it can't."""
        if not self.exe:
            return False
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return True  # it answered: one that doesn't is killed below, under this lock
            if time.time() < self.retry:  # it just failed to start: don't hold every call up trying again
                return False
            with socket.create_server(("127.0.0.1", 0)) as s:
                port = s.getsockname()[1]
            self.url = f"http://127.0.0.1:{port}"
            config = {"model_list": [], "litellm_settings": {"drop_params": True},
                      "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY"}}
            for p, (base, _) in self.keys.items():
                var = p.upper()
                config["model_list"].append({"model_name": f"{p}/*", "litellm_params": {
                    "model": f"{p}/*", "api_key": f"os.environ/{var}_KEY",
                    **({"api_base": f"os.environ/{var}_BASE"} if base else {})}})
            (self.dir / "litellm.json").write_text(json.dumps(config, indent=1))  # names of variables, never keys
            env = {"PATH": "/usr/bin:/bin", "HOME": str(self.dir), "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                   "LITELLM_MASTER_KEY": self.master, "LITELLM_LOG": "ERROR", "NO_DOCS": "True", "NO_REDOC": "True",
                   **{f"{p.upper()}_{k}": v for p, (base, key) in self.keys.items()
                      for k, v in (("BASE", base and base + "/v1" if p == "openai" else base), ("KEY", key)) if v}}
            with open(self.dir / "litellm.log", "ab") as log:  # startup errors only: it logs no calls
                try:
                    self.proc = subprocess.Popen([self.exe, "--config", str(self.dir / "litellm.json"), "--host",
                                                  "127.0.0.1", "--port", str(port)], env=env, cwd=self.dir,
                                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                except OSError as e:
                    log.write(f"could not start {self.exe}: {e}\n".encode())
                    self.retry = time.time() + 30
                    return False
            up = False
            try:
                (self.dir / "litellm.pid").write_text(str(self.proc.pid))  # for end_last(), after a killed server
                end = time.time() + STARTUP
                while not up and self.proc.poll() is None and time.time() < end:
                    try:
                        with OPENER.open(self.url + "/health/liveliness", timeout=5):
                            up = True
                    except (OSError, http.client.HTTPException):
                        time.sleep(0.5)
            except OSError as e:
                print(f"swarmory: could not record LiteLLM's pid: {e}", file=sys.stderr, flush=True)
            finally:
                if not up:  # it died, was too slow or left no pid file: a call after the back-off starts another
                    self.proc.kill()
                    self.proc.wait()
                    self.retry = time.time() + 30
            return up

    def end_last(self):
        """Kills the LiteLLM that a server killed on this data left running with the providers' keys: the pid the last
        one recorded, if it still runs with this data's config."""
        try:
            pid = int((self.dir / "litellm.pid").read_text())
        except (OSError, ValueError):
            return
        ps = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
        if str(self.dir / "litellm.json") in ps:
            os.kill(pid, signal.SIGKILL)

    def stop(self):
        """Ends LiteLLM, which holds the providers' keys: asked first, and killed if a call still holds it open."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def find(self, model: str | None) -> tuple:
        """(provider, entry) for `model`: the provider that serves it and its entry in the price table, each None if
        unknown. A name with the `provider/` prefix of any provider the price table names is that provider's, and is
        looked up as it is and without the prefix. Any other name is looked up as it is and under the prefix of
        each of PROVIDERS and of each provider whose key the server holds, as a job's coding agent may drop
        it. Both come from one row, the
        first that names a provider the server knows, so a call is priced as the model it is served as: plain
        `gemini-2.5-pro` is listed for another service, and is served and priced as `gemini/gemini-2.5-pro`. A name
        no known provider serves keeps its own row's price. An entry with no input price is no entry."""
        priced = lambda e: e if e and e.get("input_cost_per_token") is not None else None  # noqa: E731
        prefix, slash, name = (model or "").partition("/")
        if slash and prefix in self.known:
            names = [(model, prefix), (name, prefix)]
        else:
            prefix, names = None, [(model, None), *((f"{p}/{model}", p) for p in {**PROVIDERS, **self.keys})]
        for n, under in names:
            e = self.table.get(n)
            provider = e and (under or (e.get("litellm_provider") if e.get("litellm_provider") in PROVIDERS else None))
            if provider:
                return provider, priced(e)
        return prefix, priced(self.table.get(model))

    def translates(self, route: str, provider: str | None, model: str):
        """The name LiteLLM serves `model` under, `<provider>/<model>`, for a call to `route`'s API when the model's
        provider is another whose key the server holds; else None, and the call goes to the route's own
        upstream as it is."""
        if not self.exe or provider == route or provider not in self.keys:
            return None
        return model if model.startswith(provider + "/") else f"{provider}/{model}"


def price(e: dict | None, used: dict):
    """A call's cost in dollars by LiteLLM's rules, from the model's entry in its price table, or None without one.
    Past a long-context tier (the whole prompt, cached and written parts too, over N thousand tokens) every rate that
    has an `_above_Nk_tokens` form takes it. A missing cache rate is the input rate, and a missing rate for writes to
    the hour-long cache is that of other cache writes."""
    if e is None:
        return None
    prompt = used["input"] + used["cached"] + used["written"]
    tier = max((n for n in (int(m[1]) for m in map(TIER.match, e) if m) if prompt > n * 1000), default=None)

    def rate(name: str, missing: float = 0.0) -> float:
        for key in ([f"{name}_above_{tier}k_tokens"] if tier else []) + [name]:
            if e.get(key) is not None:
                return e[key]
        return missing
    fresh, hour = rate("input_cost_per_token"), used.get("written_1h", 0)
    write = rate("cache_creation_input_token_cost", fresh)
    return (used["input"] * fresh + used["cached"] * rate("cache_read_input_token_cost", fresh)
            + (used["written"] - hour) * write + hour * rate("cache_creation_input_token_cost_above_1hr", write)
            + used["output"] * rate("output_cost_per_token"))
