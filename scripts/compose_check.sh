#!/usr/bin/env bash
# compose_check.sh -- validate compose.yaml and, where Docker is available, run the demo
# service end to end and confirm two specific health signals in its output.
#
#     scripts/compose_check.sh
#
# Steps:
#   1. `docker compose config -q` -- the compose file must be well-formed.
#   2. If the Docker daemon is reachable: `docker compose up --build --abort-on-container-exit
#      engine`, then grep the combined output for "0 misbehaved" (every gate probe behaved,
#      from scripts/run_instance.py's gate report) and for the health line's
#      `"DRIFT_confirmed_without_valid_cite": [{"n": 0}]` (no confirmed fact lacking a valid
#      citation).
#
# Exit 0: everything this script could run passed.
# Exit non-zero: something it could run failed, OR it could not execute a step -- printed
# as a line starting "NOT EXECUTED:" so the caller can tell "failed" from "untested" apart.
set -u
cd "$(dirname "$0")/.." || exit 1

if ! command -v docker >/dev/null 2>&1; then
    echo "NOT EXECUTED: docker is not installed on this machine; no validation ran at all"
    exit 1
fi

echo "==> docker compose config -q"
if ! docker compose config -q; then
    echo "FAIL: compose.yaml did not validate"
    exit 1
fi
echo "OK: compose.yaml is well-formed"

if ! docker info >/dev/null 2>&1; then
    echo "NOT EXECUTED: the docker CLI is present but the daemon is not reachable (is Docker Desktop/dockerd running?); only config validation ran"
    exit 1
fi

echo "==> docker compose up --build --abort-on-container-exit engine"
out="$(mktemp)"
trap 'rm -f "$out"' EXIT
docker compose up --build --abort-on-container-exit engine 2>&1 | tee "$out"
docker compose down >/dev/null 2>&1

fail=0
if ! grep -q '0 misbehaved' "$out"; then
    echo "FAIL: expected 'misbehaved' count of 0 not found in engine output"
    fail=1
fi
if ! grep -qF '"DRIFT_confirmed_without_valid_cite": [{"n": 0}]' "$out"; then
    echo 'FAIL: expected health line "DRIFT_confirmed_without_valid_cite": [{"n": 0}] not found in engine output'
    fail=1
fi

if [ "$fail" -ne 0 ]; then
    exit 1
fi
echo "OK: demo ran clean end to end (0 misbehaved gates; no confirmed fact without a valid citation)"
exit 0
