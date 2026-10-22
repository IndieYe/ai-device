"""Protocol-independent local implementation for the apply_patch tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..patch import PatchError, execute_patch, parse_patch
from ..patch.models import PatchDocument
from ..responses import build_action_summary
from .results import FileResult


def _build_patch_summary(document: PatchDocument) -> str | None:
    parts: list[str] = []
    for operation in document.operations:
        source_name = Path(operation.path).name
        if operation.type == "add":
            parts.append(build_action_summary("Add", source_name))
            continue
        if operation.type == "delete":
            parts.append(build_action_summary("Delete", source_name))
            continue
        if operation.move_to:
            target_name = Path(operation.move_to).name
            move_summary = f"Move {source_name} → {target_name}"
            parts.append(move_summary)
            continue
        parts.append(build_action_summary("Update", source_name))
    if not parts:
        return None
    return "; ".join(parts)


def _build_patch_meta(document: PatchDocument) -> dict[str, Any]:
    paths: list[str] = []
    for operation in document.operations:
        paths.append(operation.path)
        if operation.move_to:
            paths.append(operation.move_to)
    meta: dict[str, Any] = {"paths": paths}
    summary = _build_patch_summary(document)
    if summary:
        meta["summary"] = summary
    return meta


def apply_patch(patch: str, dry_run: bool = False) -> FileResult:
    """
    Apply a custom patch DSL to UTF-8 text files.

    Patch text must start with `*** Begin Patch` and end with
    `*** End Patch`. Supported operations are `*** Add File: <path>`,
    `*** Delete File: <path>`, `*** Update File: <path>`, and optional
    `*** Move to: <path>` inside an update block.
    """

    try:
        document = parse_patch(patch)
        result = execute_patch(document, dry_run=dry_run)
        return FileResult(data=result, metadata=_build_patch_meta(document))
    except PatchError as exc:
        error_result = {
            "success": False,
            "error": exc.to_dict(),
            "applied": False,
        }
        return FileResult(data=error_result, metadata={"error_code": exc.code})
