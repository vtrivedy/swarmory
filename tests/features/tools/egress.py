"""Prints `egress <your id> example.com=<status> api.anthropic.com=<status>`: what this machine reaches. A Modal run
allows example.com (--allow) and nothing else outside Modal, so the second reads `blocked`."""
import os
import urllib.error
import urllib.request


def status(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return str(r.status)
    except urllib.error.HTTPError as e:
        return str(e.code)
    except OSError:
        return "blocked"


print(f"egress {os.environ.get('SWARMORY_ID', '?')} example.com={status('https://example.com')} "
      f"api.anthropic.com={status('https://api.anthropic.com/v1/models')}")
