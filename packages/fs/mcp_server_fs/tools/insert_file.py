"""MCP adapter for the local insert_file operation."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from .. import core
from typing import Optional
from ..config import TEMP_DIR
from .results import to_tool_result


def insert_file(
    path: str,
    text: str,
    line: Optional[int] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
    at_end: bool = False,
    dry_run: bool = False,
    include_diff: bool = False,
) -> ToolResult:
    """
    在文件指定位置插入文本。

    这个工具适合单文件、小范围插入操作，支持四种定位方式：
    - `line`
    - `before`
    - `after`
    - `at_end`

    Args:
        path: 文件路径。支持绝对路径、相对路径和 `~`。
        text: 要插入的文本。
        line: 默认 `None`。1-based 行号，在该行前插入；等于总行数+1 时表示追加到末尾。
        before: 默认 `None`。在首次匹配到的文本前插入。
        after: 默认 `None`。在首次匹配到的文本后插入。
        at_end: 默认 `False`。为 `True` 时直接追加到文件末尾。
        dry_run: 默认 `False`。为 `True` 时只计算结果，不落盘。
        include_diff: 默认 `False`。为 `True` 时在响应中附带 unified diff；
            非必要情况下建议保持关闭，以减少 token 消耗。

    Notes:
        `line`、`before`、`after`、`at_end` 必须且只能提供一种定位方式。

    Returns:
        JSON 格式字符串。
    """
    return to_tool_result(
        core.insert_file(
            path,
            text,
            line,
            before,
            after,
            at_end,
            dry_run,
            include_diff,
            temp_dir=TEMP_DIR,
        )
    )


def register_tool(mcp: FastMCP):
    """Register the insert_file tool."""
    return mcp.tool()(insert_file)
