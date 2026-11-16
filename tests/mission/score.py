"""The hidden grader for tests/mission: python3 score.py REPO_DIR. Checks greet() and shout() from REPO_DIR/greet.py
on cases that follow from BRIEF.md but are not its examples, and prints `score: <fraction passed>`."""
import importlib.util
import sys
from pathlib import Path

CASES = {"greet": [("Linus", "Hello, Linus!"), ("\tAda Lovelace\n", "Hello, Ada Lovelace!"), ("   ", "Hello, world!"),
                   ("Grace", "Hello, Grace!")],
         "shout": [("done!!!", "DONE!"), (" ok ", "OK!"), ("...", ""), ("Is it?", "IS IT!"), ("go", "GO!")]}

passed = total = 0
try:
    spec = importlib.util.spec_from_file_location("greet", Path(sys.argv[1]) / "greet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
except Exception as e:
    print(f"could not import greet.py: {type(e).__name__}: {e}")
    module = None
for name, cases in CASES.items():
    for arg, want in cases:
        total += 1
        try:
            got = getattr(module, name)(arg)
        except Exception as e:
            got = f"{type(e).__name__}: {e}"
        passed += got == want
        print(f"{'ok  ' if got == want else 'FAIL'} {name}({arg!r}) = {got!r}"
              + ("" if got == want else f", want {want!r}"))
print(f"score: {round(passed / total, 2)}")
