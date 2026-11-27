# Evidence for the pieces design

See [../COMMENTARY.md](../COMMENTARY.md), section 9, for what each file shows.

- `demo/`: runs against this branch. `squads.py` is a 52-line piece; `demo.py` prints the hold, replay and ownership scenarios; `test_git.py` asserts per-member push and fetch rules through real `git http-backend`.
- `audit-repros/`: runs against `07932e6`, before this branch. Reproductions of the bugs that ruled out an earlier design, and `tla/` with the delivery model plus a naive Hold/Release that TLC rejects.

From the repo root:

    SWARMORY_REPO=. python3 docs/pieces/evidence/demo/demo.py
    SWARMORY_REPO=. uvx --python 3.12 --with-editable . pytest docs/pieces/evidence/demo/test_git.py -q

    git worktree add /tmp/base 07932e6
    SWARMORY_REPO=/tmp/base python3 docs/pieces/evidence/audit-repros/hold_release_demo.py
    SWARMORY_REPO=/tmp/base uvx --python 3.12 --with-editable /tmp/base pytest docs/pieces/evidence/audit-repros/test_gzip_push.py -q

These are evidence for review, not part of the test suite; `pytest tests` does not collect them.
