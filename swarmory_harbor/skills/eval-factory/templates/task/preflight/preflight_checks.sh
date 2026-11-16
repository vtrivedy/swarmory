#!/bin/bash
# Run inside the built image as the solver's user, before any paid attempt. Prints one JSON line per check.
set -u
status=0

ok() { printf '{"check":"%s","status":"ok"}\n' "$1"; }
blocked() { printf '{"check":"%s","status":"blocked","detail":"%s"}\n' "$1" "$2"; status=1; }

[ "$(id -u)" != "0" ] && ok "not_root" || blocked "not_root" "solver runs as root"
python3 -c 'print(1)' >/dev/null 2>&1 && ok "python_runs" || blocked "python_runs" "python3 failed"
ls /tests >/dev/null 2>&1 && blocked "tests_hidden" "solver can list /tests" || ok "tests_hidden"
ls /solution >/dev/null 2>&1 && blocked "solution_hidden" "solver can list /solution" || ok "solution_hidden"
# A real fetch, not a TCP connect. Under a domain allowlist, Modal accepts TCP to any host and resets the TLS handshake.
python3 -c 'import urllib.request; urllib.request.urlopen("https://example.com", timeout=5)' >/dev/null 2>&1 \
	&& blocked "network_off" "solver fetched https://example.com" || ok "network_off"
python3 -c 'import urllib.request; urllib.request.urlopen("http://example.com", timeout=5)' >/dev/null 2>&1 \
	&& blocked "network_off_http" "solver fetched http://example.com" || ok "network_off_http"
ls /logs/verifier/reward.* >/dev/null 2>&1 && blocked "no_reward_file" "a reward file exists" || ok "no_reward_file"
writable=$(find / \( -path /proc -o -path /sys \) -prune -o -type s -writable -print 2>/dev/null | head -n 5)
[ -z "$writable" ] && ok "no_writable_sockets" || blocked "no_writable_sockets" "$(echo $writable)"
test -w /app/output && ok "output_writable" || blocked "output_writable" "solver cannot write /app/output"

exit $status
