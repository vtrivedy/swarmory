"""control_plane/traces.py: a Masker hides the server's keys and logins, the run's tokens and what is shaped like a
key, and nothing else."""
import json

from swarmory.control_plane import traces
from swarmory.control_plane.policy import sha

SECRET = "sk-ant-api03-" + "x" * 40


def test_masking_hides_keys_tokens_and_logins_and_nothing_else():
    token = "ab" * 16
    login = json.dumps({"tokens": {"access_token": "eyJaccess.token.value", "refresh_token": "rt-123456789"}})
    mask = traces.masker({"ANTHROPIC_API_KEY": SECRET, "CODEX_AUTH_JSON": login, "HOME": "/home/x"}, {sha(token), "v"})
    said = (f"key {SECRET} token {token} other cd" + "ef" * 15 + " rt-123456789 MODAL_TOKEN_SECRET=as-abcdefgh"
            "ijklmnopq ghp_" + "a" * 36 + " AKIAABCDEFGHIJKLMNOP commit 0123456789abcdef0123456789abcdef01234567")
    assert mask(said) == ("key [hidden] token [hidden] other cd" + "ef" * 15 + " [hidden] MODAL_TOKEN_SECRET=[hidden]"
                          " [hidden] [hidden] commit 0123456789abcdef0123456789abcdef01234567")
    assert mask("/home/x is HOME") == "/home/x is HOME"
