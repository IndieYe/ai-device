"""Protocol-independent local implementation for the write_file tool."""

from __future__ import annotations

from ..io_utils import atomic_write_text
from ..path_utils import validate_path
from ..responses import (
    build_action_summary,
    build_error_response,
    build_success_response,
)
from .results import FileResult


def write_file(path: str, content: str) -> FileResult:
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
        FileResult containing a plain data dictionary and operation metadata.
    """

    try:
        file_path = validate_path(path)
        atomic_write_text(file_path, content)
        response = build_success_response(
            dry_run=False,
            summary={
                "files_written": 1,
                "bytes_written": len(content.encode("utf-8")),
            },
            operations=[
                {
                    "type": "write_file",
                    "path": str(file_path),
                    "status": "applied",
                    "bytes_written": len(content.encode("utf-8")),
                }
            ],
        )
        return FileResult(
            data=response,
            metadata={
                "summary": build_action_summary("write", str(path)),
                "path": str(file_path),
            },
        )
    except Exception as exc:
        response = build_error_response(
            code="WRITE_FILE_ERROR",
            message="Failed to write file",
            details={
                "path": path,
                "reason": str(exc),
                "exception_type": type(exc).__name__,
                "bytes_requested": len(content.encode("utf-8")),
            },
        )
        return FileResult(
            data=response,
            metadata={
                "summary": build_action_summary("write", str(path)),
                "path": str(path),
            },
        )
