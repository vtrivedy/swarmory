#!/bin/bash
set -euo pipefail
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt /logs/verifier/grade.json
test ! -e /logs/verifier/reward.json
test ! -e /logs/verifier/reward.txt
chmod -R go-rwx /tests
python3 -I /tests/verifier.py /app/output /logs/verifier
