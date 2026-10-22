"""MCP adapter for the local edit_file operation."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from .. import core
from ..config import TEMP_DIR
from .results import to_tool_result
from pydantic import BaseModel, ConfigDict, Field


class EditOperation(BaseModel):
    """Strict replacement input for the MCP schema."""

    model_config = ConfigDict(extra="forbid", populate_by_name=False)
    old_text: str = Field(alias="oldText")
    new_text: str = Field(alias="newText")


def edit_file(
    path: str,
    edits: list[EditOperation],
    dry_run: bool = False,
    include_diff: bool = False,
) -> ToolResult:
    """
    对文本文件执行精确文本替换。

    这个工具按 `oldText -> newText` 顺序逐条替换，每条替换只应用一次，
    适合小范围、可精确命中的单文件文本修改。

    Args:
        path: 文件路径。支持绝对路径、相对路径和 `~`。
        edits: 编辑操作列表。每项必须包含 `oldText` 和 `newText`，
            并按给定顺序依次执行精确匹配替换。
        dry_run: 默认 `False`。为 `True` 时只计算结果，不落盘。
        include_diff: 默认 `False`。为 `True` 时在响应中附带 unified diff；
            非必要情况下建议保持关闭，以减少 token 消耗。

    Returns:
        JSON 格式字符串。
    """
    return to_tool_result(
        core.edit_file(
            path,
            [
                edit.model_dump(by_alias=True)
                if isinstance(edit, EditOperation)
                else dict(edit)
                for edit in edits
            ],
            dry_run,
            include_diff,
            temp_dir=TEMP_DIR,
        )
    )


def register_tool(mcp: FastMCP):
    """Register the edit_file tool."""
    return mcp.tool()(edit_file)
