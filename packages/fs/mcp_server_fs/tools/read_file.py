"""MCP adapter for the local read_file operation."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from .. import core
from typing import Literal, Optional
from ..defaults import DEFAULT_MAX_CHARS
from .results import to_tool_result


def read_file(
    path: str,
    type: Literal["chars", "lines"] = "chars",
    offset: int = 0,
    limit: Optional[int] = None,
    direction: Literal["forward", "backward"] = "forward",
    max_chars: int = DEFAULT_MAX_CHARS,
) -> ToolResult:
    """
    读取文本文件内容

    Args:
        path: 文件路径
        type: 读取类型，默认 "chars"；可选 "chars"(字符), "lines"(行)
        offset: 起始位置，默认 0；单位随 type 变化（字符/行号，从0开始）
        limit: 读取数量，默认 None 表示读取到末尾；单位随 type 变化（字符数/行数）
        direction: 读取方向，默认 "forward"；可选 "forward"(从前往后), "backward"(从后往前)
        max_chars: 返回内容的最大字符数限制（默认 10000）

    Returns:
        JSON格式的结果，包含文件内容和元数据
    """
    return to_tool_result(
        core.read_file(path, type, offset, limit, direction, max_chars)
    )


def register_tool(mcp: FastMCP):
    """Register the read_file tool."""
    return mcp.tool()(read_file)
