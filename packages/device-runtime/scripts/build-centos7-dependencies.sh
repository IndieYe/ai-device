#!/usr/bin/env bash
# Explicit opt-in build only. Never run against a real client or mount host sockets.
set -euo pipefail
if [[ $# != 1 || -e "$1" ]]; then
  echo 'usage: build-centos7-dependencies.sh NEW_OUTPUT_DIRECTORY' >&2
  exit 2
fi
command -v docker >/dev/null || { echo "Docker is required; no output directory created" >&2; exit 2; }
mkdir -p "$1"
OUTPUT=$(cd "$1" && pwd)
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
IMAGE='quay.io/pypa/manylinux2014_x86_64@sha256:f6a6f153c9cf45470507ebbc27013e5db14ce21331b62fd2c540561f25e5f0a5'
# Explicit build-scoped proxy only; never inherit credentials or host loopback silently.
PROXY_ARGS=()
if [[ -n "${BUILD_HTTPS_PROXY:-}" ]]; then
  PROXY_ARGS=(--env "HTTPS_PROXY=$BUILD_HTTPS_PROXY" --env "HTTP_PROXY=$BUILD_HTTPS_PROXY")
fi
# Source snapshot only; input is read-only, output is the sole writable host mount.
docker run "${PROXY_ARGS[@]}" --rm --platform linux/amd64 --cpus 2 --memory 4g --pids-limit 256 \
  --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,source=$SCRIPT_DIR,target=/input,readonly" \
  --mount "type=bind,source=$OUTPUT,target=/output" \
  "$IMAGE" bash /input/centos7-build-inside.sh
