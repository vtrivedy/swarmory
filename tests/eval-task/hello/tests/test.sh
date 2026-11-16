#!/bin/bash
if [ "$(cat /app/output/answer.txt 2>/dev/null)" = hello ]; then reward=1; else reward=0; fi
echo $reward > /logs/verifier/reward.txt
