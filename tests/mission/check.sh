#!/bin/bash
# bash check.sh [greet|shout]: checks greet.py against the examples in BRIEF.md. Exit 0 when every check passes.
cd "$(dirname "$0")" && python3 -B - "${1:-all}" <<'PY'  # -B: no __pycache__ to commit by mistake
import sys
import greet

CASES = {"greet": [("Ada", "Hello, Ada!"), ("  Grace ", "Hello, Grace!"), ("", "Hello, world!")],
         "shout": [("hello", "HELLO!"), ("ship it.", "SHIP IT!"), ("why?!", "WHY!"), ("  ", "")]}
which = [sys.argv[1]] if sys.argv[1] in CASES else list(CASES)
failed = 0
for name in which:
    for arg, want in CASES[name]:
        try:
            got = getattr(greet, name)(arg)
        except Exception as e:
            got = f"{type(e).__name__}: {e}"
        if got != want:
            failed += 1
            print(f"FAIL {name}({arg!r}) is {got!r}, want {want!r}")
print(f"{'ok' if not failed else f'{failed} failed'}: {', '.join(which)}")
sys.exit(1 if failed else 0)
PY
