"""Protocol-independent local implementation for the read_file tool."""

from __future__ import annotations

import builtins
from typing import Literal

from ..defaults import DEFAULT_MAX_CHARS
from ..formatting import format_size
from ..path_utils import validate_path
from ..responses import build_action_summary
from .results import FileResult


def read_file(
    path: str,
    type: Literal["chars", "lines"] = "chars",
    offset: int = 0,
    limit: int | None = None,
    direction: Literal["forward", "backward"] = "forward",
    max_chars: int = DEFAULT_MAX_CHARS,
) -> FileResult:
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
    if type not in {"chars", "lines"}:
        return FileResult(
            data={
                "error": "ValueError",
                "message": f"Unsupported read type: {type}",
                "path": path,
            },
            metadata={
                "summary": build_action_summary("read", str(path)),
                "path": str(path),
            },
        )

    try:
        if offset < 0 or (limit is not None and limit < 0):
            raise ValueError("offset and limit must be non-negative")
        file_path = validate_path(path)
        if not file_path.exists():
            return FileResult(
                data={
                    "error": "FileNotFoundError",
                    "message": f"File not found: {path}",
                    "path": str(file_path),
                },
                metadata={
                    "summary": build_action_summary("read", str(path)),
                    "path": str(file_path),
                },
            )
        if not file_path.is_file():
            return FileResult(
                data={
                    "error": "NotAFileError",
                    "message": f"Path is not a file: {path}",
                    "path": str(file_path),
                },
                metadata={
                    "summary": build_action_summary("read", str(path)),
                    "path": str(file_path),
                },
            )
        file_size = file_path.stat().st_size
    except Exception as exc:
        return FileResult(
            data={
                "error": builtins.type(exc).__name__,
                "message": str(exc),
                "path": path,
            },
            metadata={
                "summary": build_action_summary("read", str(path)),
                "path": str(path),
            },
        )

    try:
        result = {
            "path": str(file_path),
            "size": file_size,
            "size_formatted": format_size(file_size),
        }

        if type == "lines":
            with open(file_path, "r", encoding="utf-8") as file_obj:
                all_lines = file_obj.readlines()

            total_lines = len(all_lines)
            result["total_lines"] = total_lines
            if direction == "backward":
                end_idx = max(0, total_lines - offset)
                start_idx = max(0, end_idx - limit) if limit is not None else 0
            else:
                start_idx = min(offset, total_lines)
                end_idx = (
                    min(total_lines, start_idx + limit)
                    if limit is not None
                    else total_lines
                )
            selected_lines = all_lines[start_idx:end_idx]

            content = "".join(selected_lines)
            result["lines_read"] = len(selected_lines)
            result["line_range"] = (
                f"{start_idx + 1}-{end_idx}" if selected_lines else None
            )
        else:
            with open(file_path, "r", encoding="utf-8") as file_obj:
                all_content = file_obj.read()

            total_chars = len(all_content)
            result["total_chars"] = total_chars
            if direction == "backward":
                end_idx = max(0, total_chars - offset)
                start_idx = max(0, end_idx - limit) if limit is not None else 0
            else:
                start_idx = min(offset, total_chars)
                end_idx = (
                    min(total_chars, start_idx + limit)
                    if limit is not None
                    else total_chars
                )
            content = all_content[start_idx:end_idx]

            result["chars_read"] = len(content)

        if len(content) > max_chars:
            result["content"] = content[:max_chars]
            result["truncated"] = True
            result["total_content_chars"] = len(content)
            result["returned_chars"] = max_chars
            result["message"] = (
                f"Content truncated to {max_chars} characters. "
                "Use offset and limit parameters to read other parts."
            )
        else:
            result["content"] = content
            result["truncated"] = False

        return FileResult(
            data=result,
            metadata={
                "summary": build_action_summary("read", str(path)),
                "path": str(file_path),
            },
        )
    except UnicodeDecodeError as exc:
        return FileResult(
            data={
                "error": "UnicodeDecodeError",
                "message": f"Failed to decode file as text: {exc!s}",
                "path": str(file_path),
            },
            metadata={
                "summary": build_action_summary("read", str(path)),
                "path": str(file_path),
            },
        )
    except Exception as exc:
        return FileResult(
            data={
                "error": builtins.type(exc).__name__,
                "message": f"Error reading file: {exc!s}",
                "path": str(file_path),
            },
            metadata={
                "summary": build_action_summary("read", str(path)),
                "path": str(file_path),
            },
        )
