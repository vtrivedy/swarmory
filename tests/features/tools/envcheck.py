"""Prints `env <your id> <json>`: the first 16 hex digits of the sha256 of every variable in this environment whose
name looks like a secret's (KEY, TOKEN, SECRET, AUTH, PASSWORD), never a value. The checker matches each against the
agent's SWARMORY_TOKEN and the run's grant, so a real key on this machine shows up as a hash it can't explain."""
import hashlib
import json
import os

WORDS = ("KEY", "TOKEN", "SECRET", "AUTH", "PASSWORD")
found = {k: hashlib.sha256(v.encode()).hexdigest()[:16] for k, v in os.environ.items()
         if any(w in k.upper() for w in WORDS) and v}
print(f"env {os.environ.get('SWARMORY_ID', '?')} {json.dumps(found, sort_keys=True)}")
