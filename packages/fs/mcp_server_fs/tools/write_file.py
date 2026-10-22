"""MCP adapter for the local write_file operation."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from .. import core
from .results import to_tool_result


def write_file(path: str, content: str) -> ToolResult:
    """
    创建新文件或覆盖现有文件。

    默认返回精简的结构化结果，适合给 AI 工具调用方消费：
    - 成功时返回 `summary + operations + warnings`
    - 失败时返回 `error.code + error.message + error.details`

    这个工具适合：
    - 新建文件
    - 已知完整目标内容时整文件覆盖

    这个工具不返回 diff，因此在“明确知道要写入什么内容”的场景下，
    通常比 `edit_file` / `insert_file(include_diff=true)` 更省 token。

    Args:
        path: 文件路径。支持绝对路径、相对路径和 `~`。
        content: 要写入文件的完整文本内容。会以 UTF-8 原子写入。

    Returns:
        JSON 格式字符串。
    """
    return to_tool_result(core.write_file(path, content))


def register_tool(mcp: FastMCP):
    """Register the write_file tool."""
    return mcp.tool()(write_file)
