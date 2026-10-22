"""Protocol-independent local implementation for the insert_file tool."""

from __future__ import annotations

import difflib
from pathlib import Path

from ..io_utils import atomic_write_text
from ..path_utils import validate_path
from ..responses import (
    build_action_summary,
    build_error_response,
    build_success_response,
)
from .results import FileResult


def insert_file(
    path: str,
    text: str,
    line: int | None = None,
    before: str | None = None,
    after: str | None = None,
    at_end: bool = False,
    dry_run: bool = False,
    include_diff: bool = False,
    *,
    temp_dir: str | Path | None = None,
) -> FileResult:
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
        FileResult containing a plain data dictionary and operation metadata.
    """

    try:
        modes_used = sum(value is not None for value in (line, before, after)) + int(
            at_end
        )
        if modes_used != 1:
            raise ValueError(
                "Exactly one of line, before, after, or at_end=True must be provided"
            )

        file_path = validate_path(path)
        content = file_path.read_text(encoding="utf-8")

        if line is not None:
            if line < 1:
                raise ValueError("line must be >= 1")
            lines = content.splitlines(keepends=True)
            max_line = len(lines) + 1
            if line > max_line:
                raise ValueError(f"line {line} out of range (max {max_line})")
            insertion_index = sum(len(item) for item in lines[: line - 1])
            insertion_mode = "line"
        elif before is not None:
            insertion_index = content.find(before)
            if insertion_index == -1:
                raise ValueError("before anchor not found")
            insertion_mode = "before"
        elif at_end:
            insertion_index = len(content)
            insertion_mode = "at_end"
        else:
            anchor = after or ""
            anchor_index = content.find(anchor)
            if anchor_index == -1:
                raise ValueError("after anchor not found")
            insertion_index = anchor_index + len(anchor)
            insertion_mode = "after"

        modified_content = content[:insertion_index] + text + content[insertion_index:]
        diff_text = "\n".join(
            difflib.unified_diff(
                content.splitlines(keepends=True),
                modified_content.splitlines(keepends=True),
                fromfile=path,
                tofile=path,
                lineterm="",
            )
        )

        if not dry_run:
            atomic_write_text(file_path, modified_content)

        response = build_success_response(
            dry_run=dry_run,
            include_diff=include_diff,
            temp_dir=temp_dir,
            diff_text=diff_text,
            summary={
                "files_updated": 1,
                "insertions_applied": 1,
                "inserted_chars": len(text),
            },
            operations=[
                {
                    "type": "insert_file",
                    "path": str(file_path),
                    "status": "dry_run_ok" if dry_run else "applied",
                    "mode": insertion_mode,
                    "inserted_chars": len(text),
                }
            ],
        )
        return FileResult(
            data=response,
            metadata={
                "summary": build_action_summary("insert", str(path)),
                "path": str(file_path),
            },
        )
    except Exception as exc:
        response = build_error_response(
            code="INSERT_FILE_ERROR",
            message="Failed to insert text into file",
            details={
                "path": path,
                "reason": str(exc),
                "exception_type": type(exc).__name__,
                "line": line,
                "before": before,
                "after": after,
                "at_end": at_end,
                "dry_run": dry_run,
                "include_diff": include_diff,
                "inserted_chars": len(text),
            },
        )
        return FileResult(
            data=response,
            metadata={
                "summary": build_action_summary("insert", str(path)),
                "path": str(path),
            },
        )
