#!/usr/bin/env bash
# Runs the Playwright tests inside the official Playwright image (Chromium needs system libraries that a
# plain WSL Ubuntu does not have). Usage, from dashboard/:
#   e2e/run.sh            layout test: starts a private broker on :19001, removes it afterwards
#   SHOTS=1 e2e/run.sh    docs/ screenshots from the running stack on :8080
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE=mcr.microsoft.com/playwright:v1.48.2-jammy
BROKER=edgeguard-layout-broker

if [ "${SHOTS:-0}" != "1" ]; then
  docker rm -f "$BROKER" >/dev/null 2>&1 || true
  docker run -d --rm --name "$BROKER" -p 19001:9001 \
    -v "$PWD/../mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" eclipse-mosquitto:2 >/dev/null
  trap 'docker rm -f "$BROKER" >/dev/null 2>&1 || true' EXIT
  sleep 1
fi

# Host network: the browser reaches the dev server, the private broker and the stack on localhost.
docker run --rm --network host --ipc host -u "$(id -u):$(id -g)" -e HOME=/tmp -e SHOTS="${SHOTS:-0}" ${E2E_MATCH:+-e E2E_MATCH="$E2E_MATCH"} \
  -v "$PWD/..:/repo" -w /repo/dashboard "$IMAGE" npx playwright test "$@"
