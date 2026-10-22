#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec pnpm dlx @modelcontextprotocol/inspector \
  uv run --directory "$project_dir" --extra mcp mcp-server-fs "$@"
