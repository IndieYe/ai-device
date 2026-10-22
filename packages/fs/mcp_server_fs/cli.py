"""CLI entrypoint for the MCP filesystem server."""

from __future__ import annotations

import argparse
import os
import sys

from .config import DEFAULT_MAX_CHARS, TEMP_DIR
from .formatting import format_size
from .path_utils import normalize_path, validate_path
from .io_utils import save_to_temp_file
from .responses import truncate_with_file_fallback
from .server import create_mcp

mcp, _registered_tools = create_mcp()

read_file = _registered_tools["read_file"]
read_image = _registered_tools["read_image"]
write_file = _registered_tools["write_file"]
edit_file = _registered_tools["edit_file"]
insert_file = _registered_tools["insert_file"]
apply_patch = _registered_tools["apply_patch"]


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="MCP Filesystem Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default=None,
        help="Transport mode: stdio (default) or sse (HTTP Server-Sent Events). Can be set via MCP_TRANSPORT env var.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Host to bind to (only for SSE mode). Can be set via MCP_HOST env var. Default: 127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind to (only for SSE mode). Can be set via MCP_PORT env var. Default: 8000",
    )
    return parser



def main() -> None:
    """Main entrypoint."""
    parser = build_parser()
    args = parser.parse_args()

    transport = args.transport or os.getenv("MCP_TRANSPORT", "stdio")
    host = args.host or os.getenv("MCP_HOST", "127.0.0.1")
    port = args.port or int(os.getenv("MCP_PORT", "8000"))

    if transport == "sse":
        print(f"Starting MCP server in SSE mode on {host}:{port}", file=sys.stderr)
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport="stdio")
