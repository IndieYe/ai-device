"""Structured response helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .defaults import DEFAULT_MAX_CHARS
from .io_utils import save_to_temp_file


def truncate_with_file_fallback(
    content: str,
    max_chars: int,
    result_type: str = "result",
    *,
    temp_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Truncate content and persist the full text to a temp file when needed."""
    if len(content) <= max_chars:
        return {
            "content": content,
            "truncated": False,
            "total_chars": len(content),
        }

    temp_file_path = save_to_temp_file(content, prefix=result_type, temp_dir=temp_dir)
    return {
        "content": content[:max_chars],
        "truncated": True,
        "total_chars": len(content),
        "temp_file": temp_file_path,
        "message": (
            f"Content exceeds {max_chars} characters. Full content saved to "
            f"{temp_file_path}. Use read_file to read it in chunks."
        ),
    }


def json_response(payload: dict[str, Any]) -> str:
    """Serialize a tool response payload as formatted JSON."""
    return json.dumps(payload, indent=2, ensure_ascii=False)


def build_action_summary(action: str, target: str) -> str:
    """Build a short action-oriented tool summary."""
    summary = f"{action} {Path(target).name}"
    return summary


def build_success_response(
    *,
    summary: dict[str, Any],
    operations: list[dict[str, Any]],
    dry_run: bool = False,
    warnings: list[str] | None = None,
    diff_text: str | None = None,
    include_diff: bool = False,
    temp_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build a structured success response for filesystem tools."""
    payload: dict[str, Any] = {
        "success": True,
        "dry_run": dry_run,
        "summary": summary,
        "operations": operations,
        "warnings": warnings or [],
    }
    if include_diff:
        payload["diff"] = truncate_with_file_fallback(
            diff_text or "",
            DEFAULT_MAX_CHARS,
            result_type="diff",
            temp_dir=temp_dir,
        )
    return payload


def build_error_response(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    applied: bool = False,
) -> dict[str, Any]:
    """Build a structured error response for filesystem tools."""
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
        "applied": applied,
    }
