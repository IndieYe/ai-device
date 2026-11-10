"""MCP adapter for the local read_image operation."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult

from .. import core
from ..image_utils import DEFAULT_MAX_IMAGE_BYTES, ImageReadError
from .cancellable import register_cancellable_tool
from .results import raise_image_tool_error, to_image_tool_result


def read_image(path: str, max_bytes: int = DEFAULT_MAX_IMAGE_BYTES) -> ToolResult:
    """Read a local image and return standard MCP text and image content.

    Args:
        path: Image path on the machine running this server.
        max_bytes: Per-call size limit. Defaults to 5 MB and cannot exceed 5 MB.

    Returns:
        A multimodal ToolResult containing image metadata and ImageContent.
    """
    try:
        return to_image_tool_result(core.read_image(path, max_bytes))
    except ImageReadError as exc:
        raise_image_tool_error(exc)


def register_tool(mcp: FastMCP):
    """Register the read_image tool."""
    return register_cancellable_tool(mcp, read_image)
