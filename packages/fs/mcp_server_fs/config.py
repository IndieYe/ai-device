"""Shared configuration values for the filesystem MCP server."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .defaults import DEFAULT_MAX_CHARS

load_dotenv()

TEMP_DIR = Path(
    os.path.expandvars(
        os.path.expanduser(
            os.getenv("MCP_TEMP_DIR", str(Path.home() / ".mcp-server-fs"))
        )
    )
).resolve()
