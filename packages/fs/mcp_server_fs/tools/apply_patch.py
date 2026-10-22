"""MCP adapter for the local apply_patch operation."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from .. import core
from .results import to_tool_result


def apply_patch(patch: str, dry_run: bool = False) -> ToolResult:
    """
    Apply a custom patch DSL to UTF-8 text files.

    Patch text must start with `*** Begin Patch` and end with
    `*** End Patch`. Supported operations are `*** Add File: <path>`,
    `*** Delete File: <path>`, `*** Update File: <path>`, and optional
    `*** Move to: <path>` inside an update block.
    """
    return to_tool_result(core.apply_patch(patch, dry_run))


def register_tool(mcp: FastMCP):
    """Register the apply_patch tool."""
    return mcp.tool()(apply_patch)
