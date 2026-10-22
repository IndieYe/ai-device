"""FastMCP server construction."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from fastmcp import FastMCP

from .tools import register_tools


def create_mcp() -> Tuple[FastMCP, Dict[str, Any]]:
    """Create the FastMCP server and register all tools."""
    mcp = FastMCP("filesystem-server")
    tools = register_tools(mcp)
    return mcp, tools
