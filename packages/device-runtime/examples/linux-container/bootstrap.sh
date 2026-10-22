#!/bin/bash
set -euo pipefail
umask 077
id
uv --version
uv --no-config venv --python 3.13 /work/harness
uv --no-config pip install --python /work/harness/bin/python --only-binary :all: --constraints /delivery/linux-bundle/constraints.txt 'ai-device[server,mcp] @ file:///delivery/wheels/ai_device-0.2.0-py3-none-any.whl'
exec /work/harness/bin/python -I /input/run-acceptance.py
