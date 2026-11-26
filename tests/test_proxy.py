"""swarmory/control_plane/proxy.py against a stand-in upstream and a stand-in LiteLLM (fake_litellm.py, beside a small
price table and a live one): answered model calls are recorded and priced from the live table, or the other when it
can't be read, budgets stop calls, a ledger line a crash
cut short costs no call, and a model named for another provider goes to LiteLLM with only its route's fields."""
import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import soon

from swarmory.control_plane.litellm import LiteLLM
from swarmory.control_plane.proxy import Proxy, add
from swarmory.control_plane.team import replay
from swarmory.control_plane.verbs import VERBS, spend_text

ANTHROPIC = [{"type": "message_start", "message": {"model": "claude-opus-5-5", "usage": {
    "input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 200, "output_tokens": 1,
    "cache_creation": {"ephemeral_5m_input_tokens": 50, "ephemeral_1h_input_tokens": 150}}}},
    {"type": "content_block_delta", "delta": {"text": "hi"}}, {"type": "message_delta", "usage": {"output_tokens": 50}}]
RESPONSES = [{"type": "response.created", "response": {"usage": None}}, {"type": "response.completed", "response": {
    "model": "gpt-6.1-sol-2026-09-01", "usage": {"input_tokens": 5000, "input_tokens_details": {"cached_tokens": 4000},
                                                 "output_tokens": 300}}}]
PRICES = {  # as LiteLLM's price table, with made-up prices a thousand times the real ones: a few calls cost dollars
    "claude-opus-5-5": {"litellm_provider": "anthropic", "input_cost_per_token": 4e-3, "output_cost_per_token": 2e-2,
                        "cache_read_input_token_cost": 2e-4, "cache_creation_input_token_cost": 5e-3,
                        "cache_creation_input_token_cost_above_1hr": 8e-3},
    "claude-sonnet-5-5": {"litellm_provider": "anthropic"},
    "gpt-6.1-sol": {"litellm_provider": "openai", "input_cost_per_token": 2e-3, "output_cost_per_token": 1e-2},
    "sample_spec": {}}
LIVE = {**PRICES, "gpt-6.1-terra": {"litellm_provider": "openai", "input_cost_per_token": 1e-3,  # only the live table
                                    "output_cost_per_token": 5e-3, "cache_read_input_token_cost": 5e-5}}


class Upstream(BaseHTTPRequestHandler):
    gate = None  # optional (entered Event, release Event) to hold a response mid-flight

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        asked = body.get("model")
        if body.get("drop_mid_stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", "10000")  # closes early -> IncompleteRead without usage
            self.end_headers()
            self.wfile.write(b'data: {"type":"response.created","response":{"usage":null}}\n\n')
            self.wfile.flush()
            return
        if limit := body.get("limit"):  # {status, headers, error}: a provider's refusal
            data = json.dumps(limit["error"]).encode()
            self.send_response(limit["status"])
            for k, v in {**limit.get("headers", {}), "Content-Length": str(len(data))}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
            return
        if self.gate and body.get("hold"):
            self.gate[0].set()
            self.gate[1].wait(10)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for e in ANTHROPIC if self.path.endswith("messages") else RESPONSES:
            if e["type"] == "message_start" and asked:  # answered by the model asked for
                e = {**e, "message": {**e["message"], "model": asked}}
            self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())


class Front(BaseHTTPRequestHandler):  # what server.py does: /<provider>/<rest> to the proxy, as x-who, for eval job
    def do_POST(self):                 # x-job with an x-budget in dollars
        provider, _, rest = self.path.lstrip("/").partition("/")
        h = self.headers
        job = h.get("x-job") and {"id": h["x-job"], "tokens": None, "budget": float(h["x-budget"])}
        self.server.proxy.handle(self, provider, rest, h.get("x-who"), job)
        self.server.handled += 1  # the caller may read the reply's end before the call is recorded: tests wait for this


@pytest.fixture
def call(tmp_path):
    up, front = ThreadingHTTPServer(("127.0.0.1", 0), Upstream), ThreadingHTTPServer(("127.0.0.1", 0), Front)
    url = f"http://127.0.0.1:{up.server_port}"
    venv = tmp_path / "venv"  # laid out as an installed LiteLLM is, with the price table beside the executable
    (venv / "bin").mkdir(parents=True)
    shutil.copy(Path(__file__).with_name("fake_litellm.py"), venv / "bin" / "litellm")
    table = venv / "lib" / "python3.12" / "site-packages" / "litellm" / "model_prices_and_context_window_backup.json"
    table.parent.mkdir(parents=True)
    table.write_text(json.dumps(PRICES))
    (tmp_path / "live.json").write_text(json.dumps(LIVE))
    env = {"SWARMORY_PROXY_PRICES": (tmp_path / "live.json").as_uri(), "SWARMORY_PROXY_ANTHROPIC": url,
           "SWARMORY_PROXY_OPENAI": url + "/base", "ANTHROPIC_API_KEY": "real-a", "OPENAI_API_KEY": "real-o",
           "SWARMORY_PROXY_LITELLM": str(venv / "bin" / "litellm")}
    front.proxy, front.handled = None, 0
    [threading.Thread(target=s.serve_forever, daemon=True).start() for s in (up, front)]

    def call(path, body, who="ada", **more):  # returns once the server has recorded the call
        handled = front.handled
        req = urllib.request.Request(f"http://127.0.0.1:{front.server_port}{path}", data=json.dumps(body).encode(),
                                     headers={"x-api-key": "run-token", "x-who": who, **more})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
        finally:
            soon(lambda: front.handled > handled)
    call.seen = lambda: [json.loads(x) for x in (tmp_path / "seen.jsonl").read_text().splitlines()]

    def restart(budget=None, subscription=False, **more):  # a new server on the same DATA
        if front.proxy:
            front.proxy.litellm.stop()
        front.proxy = call.proxy = Proxy(tmp_path, {**env, **more}, budget, LiteLLM(tmp_path, {**env, **more}),
                                         subscription)
    call.restart = restart
    call.ledger = tmp_path / "ledger.jsonl"
    call.rows = lambda: [json.loads(x) for x in call.ledger.read_text().splitlines()]
    yield call
    call.proxy.litellm.stop()
    up.shutdown(), front.shutdown()


def test_calls_are_priced_and_budgets_stop_the_run_and_the_job(call):
    call.restart(budget=4)
    assert call("/anthropic/v1/messages", {"model": "claude-opus-5-5", "stream": True})[0] == 200
    usd = 10 * 4e-3 + 1000 * 2e-4 + 50 * 5e-3 + 150 * 8e-3 + 50 * 2e-2  # $2.69: in, read, written, written 1h, out
    assert call.rows()[-1]["usd"] == pytest.approx(usd) and call.proxy.totals()["ada"]["usd"] == pytest.approx(usd)
    with open(call.ledger, "a") as f:  # a crash cut a ledger write short
        f.write('{"at": 1.0, "who": "ada", "mod')
    torn = call.ledger.read_bytes()
    by = {}
    replay(call.ledger, lambda row: add(by, row), repair=False)  # as tools/compare.py reads a ledger
    assert by["ada"]["calls"] == 1
    assert call.ledger.read_bytes() == torn, "a reader cut a line the server may still be writing"
    call.restart(budget=4)
    status, body = call("/anthropic/v1/messages", {"model": "gpt-9-unpriced"})
    assert status == 400 and "no price for model 'gpt-9-unpriced'" in body["error"]["message"]
    assert call("/anthropic/v1/messages", {"model": "claude-opus-5-5"})[0] == 200  # $5.38 now: over $4
    status, body = call("/anthropic/v1/messages", {"model": "claude-opus-5-5"}, who="ed")
    assert (status, body["error"]["message"]) == (429, "The run has spent $5.38 of its $4.00 budget.")
    call.restart(budget=4)
    status = call("/anthropic/v1/messages", {"model": "claude-opus-5-5"})[0]
    assert status == 429, "a restarted server forgot the spend"
    assert call.proxy.totals()["ada"]["calls"] == 2, "the call after the torn line wasn't counted"

    call.ledger.unlink()
    call.restart()
    job = {"x-job": "j1", "x-budget": "2"}
    assert call("/anthropic/v1/messages", {"model": "claude-opus-5-5"}, **job)[0] == 200
    status, body = call("/anthropic/v1/messages", {"model": "claude-opus-5-5"}, **job)
    assert (status, body["error"]["message"]) == (429, "Job j1 has spent $2.69 of its $2.00 budget.")
    assert call("/anthropic/v1/messages", {"model": "claude-opus-5-5"})[0] == 200, "the member's own calls go on"
    assert call("/anthropic/v1/messages", {"model": "gpt-9-unpriced"})[0] == 200, "with no budget, unpriced calls go"
    assert call.rows()[-1]["usd"] is None
    assert call.proxy.totals()["ada"]["unpriced"] == 1, "an unpriced call counted as a free one"
    assert "and 1 with no price (gpt-9-unpriced)" in spend_text({"budget": None, "by": call.proxy.totals()}, {})
    assert not (call.ledger.parent / "seen.jsonl").exists(), "pricing started LiteLLM, which only translating needs"


def test_a_model_named_for_another_provider_goes_to_litellm_with_only_its_routes_fields(call):
    call.restart()
    mark = {"cache_control": {"type": "ephemeral"}}  # as Claude Code marks its system prompt: breaks Gemini's route
    tool = {"name": "put", "input_schema": {"type": "object", "properties": {"cache_control": {"type": "string"}}}}
    used = {"type": "tool_use", "id": "t1", "name": "put", "input": {"cache_control": "kept"}}  # data, not marks
    req = {"model": "openai/gpt-6.1-sol", "max_tokens": 9, "stream": True,
           "system": [{"type": "text", "text": "be terse", **mark}], "tools": [{**tool, **mark}],
           "messages": [{"role": "user", "content": [{"type": "text", "text": "hi", **mark}]},
                        {"role": "assistant", "content": [used]},
                        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                                      "content": [{"type": "text", "text": "ok", **mark}]}]}],
           "metadata": {"tags": ["job:j9"]}, "api_base": "https://evil.example", "user_config": {"x": 1}}
    status, body = call("/anthropic/v1/messages?beta=true", req, **{"x-litellm-tags": "job:j9", "anthropic-beta": "b1"})
    assert status == 200 and b"message_start" in body
    sent = [x for x in call.seen() if "path" in x][-1]
    assert sent["path"] == "/v1/messages"
    kept = {"model": "openai/gpt-6.1-sol", "max_tokens": 9, "stream": True,
            "system": [{"type": "text", "text": "be terse"}], "tools": [tool],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]},
                         {"role": "assistant", "content": [used]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                                       "content": [{"type": "text", "text": "ok"}]}]}]}
    assert sent["body"] == kept, \
        "a field LiteLLM reads for its upstream, keys or billing, or a cache mark, went through"
    assert sent["headers"]["authorization"] == "Bearer " + call.proxy.litellm.master
    assert not any(k.startswith("x-litellm") or k == "anthropic-beta" for k in sent["headers"]), "a caller's header"
    row = call.rows()[-1]
    assert (row["via"], row["model"], row["input"], row["output"]) == ("litellm", "openai/gpt-6.1-sol", 30, 7)
    assert row["usd"] == pytest.approx(30 * 2e-3 + 7 * 1e-2)
    assert call("/openai/v1/responses", {"model": "openai/gpt-6.1-sol"})[0] == 200
    assert "via" not in call.rows()[-1], "a model named for the route's own provider went to LiteLLM"
    assert call("/openai/v1/responses", {"model": "claude-sonnet-5-5", "input": "hi"})[0] == 200  # as Harbor's Codex
    assert [x for x in call.seen() if "path" in x][-1]["body"] == {"model": "anthropic/claude-sonnet-5-5",
                                                                   "input": "hi"}
    assert call.rows()[-1]["via"] == "litellm", "a model the price table lists for another provider went native"
    assert call("/openai/v1/responses", {"model": "gpt-6.1-sol"})[0] == 200
    assert "via" not in call.rows()[-1], "the route's own provider's model went to LiteLLM"
    rows = len(call.rows())
    assert call("/openai/v1/responses", {"model": "gemini/gemini-4-argon"})[0] == 503
    assert len(call.rows()) == rows, "a model of a provider the server holds no key for was sent somewhere"
    start = next(x for x in call.seen() if "argv" in x)
    assert [p["model_name"] for p in start["config"]["model_list"]] == ["anthropic/*", "openai/*"]
    assert {"ANTHROPIC_KEY", "OPENAI_KEY", "LITELLM_MASTER_KEY"} <= set(start["env"])
    assert not {"CLAUDE_CODE_OAUTH_TOKEN", "CODEX_AUTH_JSON", "SWARMORY_TOKEN"} & set(start["env"])
    assert "real-a" not in (call.ledger.parent / "litellm.json").read_text(), "a key in LiteLLM's config file"

    seen = len(call.seen())
    call.restart(subscription=True, CLAUDE_CODE_OAUTH_TOKEN="oauth-c")
    assert call("/anthropic/v1/messages", {"model": "openai/gpt-6.1-sol"})[0] == 200
    assert "via" not in call.rows()[-1] and len(call.seen()) == seen, "a subscription run's call went to LiteLLM"


def test_a_model_only_the_live_table_lists_is_priced_and_without_it_the_table_that_came_with_litellm_prices(call):
    call.restart()
    job = {"x-job": "j1", "x-budget": "100"}
    assert call("/openai/v1/responses", {"model": "gpt-6.1-terra"}, **job)[0] == 200, "a budgeted job's call refused"
    assert call.rows()[-1]["usd"] == pytest.approx(1000 * 1e-3 + 4000 * 5e-5 + 300 * 5e-3)  # $2.70: in, read, out

    call.restart(budget=100, SWARMORY_PROXY_PRICES=(call.ledger.parent / "gone.json").as_uri())
    status, body = call("/openai/v1/responses", {"model": "gpt-6.1-terra"})
    assert status == 400 and "no price for model 'gpt-6.1-terra'" in body["error"]["message"]
    assert call("/anthropic/v1/messages", {"model": "claude-opus-5-5"})[0] == 200
    assert call.rows()[-1]["usd"] == pytest.approx(2.69), "the table that came with LiteLLM didn't price the call"

    call.restart()
    call.proxy.litellm.load((call.ledger.parent / "gone.json").as_uri())  # a later read fails: prices stay
    assert call("/openai/v1/responses", {"model": "gpt-6.1-terra"}, **job)[0] == 200
    assert call.rows()[-1]["usd"] == pytest.approx(2.7)


def test_in_flight_budget_reservation_blocks_concurrent_overshoot_and_dropped_streams_are_marked_incomplete(call):
    call.restart(budget=4)
    # First call spends $2.69, establishing mean call cost = $2.69
    assert call("/anthropic/v1/messages", {"model": "claude-opus-5-5", "stream": True})[0] == 200
    entered, release = threading.Event(), threading.Event()
    Upstream.gate = (entered, release)
    first = {}
    try:
        t = threading.Thread(target=lambda: first.update(
            res=call("/anthropic/v1/messages", {"model": "claude-opus-5-5", "hold": True})))
        t.start()
        assert entered.wait(5)
        # While call 2 ($2.69 estimated) is in flight ($2.69 + $2.69 >= $4), call 3 must be refused
        status, body = call("/anthropic/v1/messages", {"model": "claude-opus-5-5"}, who="ed")
        assert status == 429 and "in flight" in body["error"]["message"]
        release.set()
        t.join()
        assert first["res"][0] == 200
    finally:
        Upstream.gate = None
        release.set()

    # A priced model whose HTTP 200 stream drops before usage arrives is marked incomplete, not unpriced
    call.ledger.unlink()
    call.restart()
    try:
        call("/openai/v1/responses", {"model": "gpt-6.1-sol", "drop_mid_stream": True})
    except Exception:
        pass
    assert soon(lambda: call.ledger.is_file() and len(call.rows()) == 1)
    row = call.rows()[-1]
    assert row["incomplete"] is True and row["usd"] == 0.0
    assert call.proxy.totals()["ada"]["unpriced"] == 0


def test_a_provider_limit_on_a_members_call_is_noted_until_it_resets_and_spend_reports_it(call):
    call.restart(budget=100)
    spend = VERBS["spend"].call
    run = type("Run", (), {"id": "r1", "config": {}, "proxy": call.proxy})()
    reported = lambda who: spend(run, type("Who", (), {"member": who})(), {}, None).get("limited_until")
    overloaded = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    gemini = [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [  # as Gemini streams it
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37.5s"}]}}]
    chatgpt = {"error": {"type": "usage_limit_reached", "resets_in_seconds": 7200}}
    now = time.time()
    req = {"model": "claude-opus-5-5", "limit": {"status": 429, "headers": {"retry-after": "30"}, "error": overloaded}}
    assert call("/anthropic/v1/messages", req)[0] == 429
    assert reported("ada") == pytest.approx(now + 30, abs=2) and reported("ed") is None
    assert call("/anthropic/v1/messages", {**req, "limit": {"status": 429, "error": gemini}}, who="ed")[0] == 429
    assert reported("ed") == pytest.approx(now + 37.5, abs=2)
    assert call("/openai/v1/responses", {"model": "gpt-6.1-sol", "limit": {"status": 429, "error": chatgpt}}, who="cy")[0] == 429
    assert reported("cy") == pytest.approx(now + 7200, abs=2)
    assert call("/anthropic/v1/messages", {**req, "limit": {"status": 529, "error": overloaded}}, who="ed")[0] == 529
    assert reported("ed") == pytest.approx(now + 60, abs=2), "an upstream's 529 with no delay didn't wait a minute"
    assert call("/anthropic/v1/messages", req, who="ed")[0] == 429
    assert reported("ed") == pytest.approx(now + 60, abs=2), "a shorter limit cut a longer one short"
    assert call("/anthropic/v1/messages", req, who="bo", **{"x-job": "j1", "x-budget": "100"})[0] == 429
    assert reported("bo") is None, "a job's call paused its member"
    iso = {"error": {"type": "usage_limit_reached", "resets_at": "2026-11-25T10:00:00Z"}}
    assert call("/openai/v1/responses", {"model": "gpt-6.1-sol", "limit": {"status": 429, "error": iso}}, who="di")[0] == 429
    assert reported("di") == pytest.approx(now + 60, abs=2), "an ISO resets_at was read as seconds"
    down = {"type": "error", "error": {"type": "api_error", "message": "internal"}}
    assert call("/anthropic/v1/messages", {**req, "limit": {"status": 500, "error": down}}, who="gu")[0] == 500
    assert reported("gu") == pytest.approx(now + 60, abs=2), "a provider's 500 didn't pause the member"
    assert call("/anthropic/v1/messages", {**req, "limit": {"status": 400, "error": down}}, who="hy")[0] == 400
    assert reported("hy") is None, "a refused request paused the member, whose conversation may be at fault"
    endless = {"status": 429, "headers": {"retry-after": "inf"}, "error": overloaded}
    assert call("/anthropic/v1/messages", {**req, "limit": endless}, who="fy")[0] == 429
    assert reported("fy") == pytest.approx(now + 60, abs=2)

    side = {"model": "gpt-6.1-sol", "limit": {"status": 429, "headers": {"retry-after": "5"}, "error": overloaded}}
    assert call("/openai/v1/responses", side)[0] == 429  # a side model, limited briefly, then answers
    assert call("/openai/v1/responses", {"model": "gpt-6.1-sol"})[0] == 200
    assert reported("ada") == pytest.approx(now + 30, abs=2), "a side model's answer ended the main model's limit"
    assert call("/anthropic/v1/messages", {"model": "claude-opus-5-5"})[0] == 200
    assert reported("ada") is None, "an answered call left the limit the harness retried past"
    answered = spend(run, type("Who", (), {"member": "ada"})(), {}, None)
    assert answered["answered_at"] == pytest.approx(answered["now"], abs=2)
    call.restart(budget=0.01)
    assert call("/anthropic/v1/messages", {"model": "claude-opus-5-5"}, who="al")[0] == 429
    assert reported("al") is None, "the gateway's own budget refusal counted as a provider's limit"
