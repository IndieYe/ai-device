"""Tool registration helpers."""

from __future__ import annotations

from typing import Any, Dict

from fastmcp import FastMCP

from .apply_patch import register_tool as register_apply_patch_tool
from .edit_file import register_tool as register_edit_file_tool
from .insert_file import register_tool as register_insert_file_tool
from .read_file import register_tool as register_read_file_tool
from .read_image import register_tool as register_read_image_tool
from .write_file import register_tool as register_write_file_tool


def register_tools(mcp: FastMCP) -> Dict[str, Any]:
    """Register all MCP tools and return them by name."""
    return {
        "read_file": register_read_file_tool(mcp),
        "read_image": register_read_image_tool(mcp),
        "write_file": register_write_file_tool(mcp),
        "edit_file": register_edit_file_tool(mcp),
        "insert_file": register_insert_file_tool(mcp),
        "apply_patch": register_apply_patch_tool(mcp),
    }


__all__ = ["register_tools"]
