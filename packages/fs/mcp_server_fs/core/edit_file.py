"""Protocol-independent local implementation for the edit_file tool."""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from pathlib import Path

from ..io_utils import atomic_write_text
from ..path_utils import validate_path
from ..responses import (
    build_action_summary,
    build_error_response,
    build_success_response,
)
from .results import FileResult


def edit_file(
    path: str,
    edits: list[Mapping[str, str]],
    dry_run: bool = False,
    include_diff: bool = False,
    *,
    temp_dir: str | Path | None = None,
) -> FileResult:
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
        FileResult containing a plain data dictionary and operation metadata.
    """
    try:
        edit_payloads = [dict(edit) for edit in edits]
        file_path = validate_path(path)
        content = file_path.read_text(encoding="utf-8")

        modified_content = content
        for index, edit in enumerate(edit_payloads):
            old_text = edit["oldText"]
            new_text = edit["newText"]
            if old_text in modified_content:
                modified_content = modified_content.replace(old_text, new_text, 1)
            else:
                raise ValueError(
                    f"Could not find exact match for edit at index {index}: {old_text!r}"
                )

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
                "edits_applied": len(edits),
            },
            operations=[
                {
                    "type": "edit_file",
                    "path": str(file_path),
                    "status": "dry_run_ok" if dry_run else "applied",
                    "edits_applied": len(edits),
                }
            ],
        )
        return FileResult(
            data=response,
            metadata={
                "summary": build_action_summary("edit", str(path)),
                "path": str(file_path),
            },
        )
    except Exception as exc:
        response = build_error_response(
            code="EDIT_FILE_ERROR",
            message="Failed to edit file",
            details={
                "path": path,
                "reason": str(exc),
                "exception_type": type(exc).__name__,
                "edits_requested": len(edits),
                "dry_run": dry_run,
                "include_diff": include_diff,
            },
        )
        return FileResult(
            data=response,
            metadata={
                "summary": build_action_summary("edit", str(path)),
                "path": str(path),
            },
        )
