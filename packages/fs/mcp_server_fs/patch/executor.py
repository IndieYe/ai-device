"""Executor for apply_patch."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable

from ..io_utils import atomic_write_text
from ..path_utils import validate_path
from .errors import PatchError
from .models import PatchDocument, PatchHunk, PatchOperation

MAX_PATCH_FILE_BYTES = 2 * 1024 * 1024


@dataclass
class SnapshotEntry:
    """Original file state used for rollback."""

    existed: bool
    content: str | None
    mode: int | None


@dataclass
class MatchResult:
    """Resolved position and length for a unique hunk match."""

    index: int
    length: int


def _resolve_patch_path(path_str: str) -> Path:
    try:
        return validate_path(path_str)
    except Exception as exc:
        raise PatchError(
            "PATCH_INVALID_PATH",
            f"Invalid path: {path_str}",
            {
                "path": path_str,
                "error": builtins.type(exc).__name__,
                "reason": str(exc),
            },
        ) from exc


def _validate_write_target(path: Path, *, code: str, path_str: str) -> None:
    parent = path.parent
    if parent.exists() and not parent.is_dir():
        raise PatchError(
            code,
            f"Parent path is not a directory: {parent}",
            {"path": path_str, "parent": str(parent)},
        )


def _validate_file_size(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_PATCH_FILE_BYTES:
        raise PatchError(
            "PATCH_FILE_TOO_LARGE",
            f"File exceeds size limit for apply_patch: {path}",
            {"path": str(path), "size": size, "max_size": MAX_PATCH_FILE_BYTES},
        )


def _read_text_file(path: Path, *, operation: str) -> str:
    _validate_file_size(path)
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PatchError(
            "PATCH_BINARY_FILE_NOT_SUPPORTED",
            f"{operation} only supports UTF-8 text files: {path}",
            {"path": str(path)},
        ) from exc
    if "\x00" in content:
        raise PatchError(
            "PATCH_BINARY_FILE_NOT_SUPPORTED",
            f"{operation} only supports text files without null bytes: {path}",
            {"path": str(path)},
        )
    return content


def _normalize_newlines(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _build_add_content(operation: PatchOperation) -> str:
    if not operation.added_lines:
        return ""
    return "\n".join(operation.added_lines) + "\n"


def _render_hunk_text(hunk: PatchHunk, include_kinds: Iterable[str]) -> str:
    lines = [line.text for line in hunk.lines if line.kind in include_kinds]
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _find_exact_match(
    content: str,
    old_text: str,
    *,
    path: str,
    hunk_index: int,
) -> MatchResult:
    if old_text == "":
        raise PatchError(
            "PATCH_INVALID_OPERATION",
            "Empty hunk replacements are not supported",
            {"path": path, "hunk_index": hunk_index},
        )

    candidates: list[MatchResult] = []
    start = 0
    while True:
        index = content.find(old_text, start)
        if index == -1:
            break
        candidates.append(MatchResult(index=index, length=len(old_text)))
        start = index + 1
        if len(candidates) > 1:
            break

    # Allow the final line of a hunk to match EOF even when the file does not
    # end with a trailing newline.
    if len(candidates) <= 1 and old_text.endswith("\n"):
        old_text_without_newline = old_text[:-1]
        if old_text_without_newline:
            eof_index = len(content) - len(old_text_without_newline)
            if (
                eof_index >= 0
                and content.endswith(old_text_without_newline)
                and old_text != old_text_without_newline
                and not any(
                    candidate.index == eof_index
                    and candidate.length == len(old_text_without_newline)
                    for candidate in candidates
                )
            ):
                candidates.append(
                    MatchResult(
                        index=eof_index,
                        length=len(old_text_without_newline),
                    )
                )

    if not candidates:
        raise PatchError(
            "PATCH_CONTEXT_NOT_FOUND",
            f"Failed to apply hunk in {path}: context not found",
            {"path": path, "hunk_index": hunk_index},
        )
    if len(candidates) > 1:
        raise PatchError(
            "PATCH_CONTEXT_AMBIGUOUS",
            f"Failed to apply hunk in {path}: context is ambiguous",
            {"path": path, "hunk_index": hunk_index},
        )
    return candidates[0]


def _apply_hunks(content: str, operation: PatchOperation) -> str:
    updated = _normalize_newlines(content)
    for hunk_index, hunk in enumerate(operation.hunks):
        old_text = _render_hunk_text(hunk, {"context", "delete"})
        new_text = _render_hunk_text(hunk, {"context", "add"})
        match = _find_exact_match(
            updated,
            old_text,
            path=operation.path,
            hunk_index=hunk_index,
        )
        updated = (
            updated[: match.index]
            + new_text
            + updated[match.index + match.length :]
        )
    return updated


def _validate_operation_graph(document: PatchDocument) -> None:
    source_paths: set[str] = set()
    target_paths: set[str] = set()

    for operation in document.operations:
        normalized_source = str(_resolve_patch_path(operation.path))
        if normalized_source in source_paths:
            raise PatchError(
                "PATCH_INVALID_OPERATION",
                "A path may only appear once as an operation source",
                {"path": operation.path},
            )
        source_paths.add(normalized_source)

        normalized_target = (
            str(_resolve_patch_path(operation.move_to))
            if operation.move_to
            else normalized_source
        )
        if normalized_target in target_paths:
            raise PatchError(
                "PATCH_INVALID_OPERATION",
                "Multiple operations would write to the same target path",
                {"path": operation.move_to or operation.path},
            )
        target_paths.add(normalized_target)

        if operation.type != "update" and normalized_source in target_paths - {normalized_target}:
            raise PatchError(
                "PATCH_INVALID_OPERATION",
                "Conflicting source and target paths in patch document",
                {"path": operation.path},
            )

        if operation.move_to and normalized_target in source_paths:
            raise PatchError(
                "PATCH_INVALID_OPERATION",
                "Move target conflicts with another operation source",
                {"path": operation.move_to},
            )


def _simulate_operation(
    operation: PatchOperation,
    touched_paths: set[Path],
) -> tuple[dict, Dict[Path, tuple[str | None, int | None]]]:
    source_path = _resolve_patch_path(operation.path)
    touched_paths.add(source_path)

    if operation.type == "add":
        if source_path.exists():
            raise PatchError(
                "PATCH_TARGET_ALREADY_EXISTS",
                f"Cannot add existing file: {operation.path}",
                {"path": operation.path},
            )
        _validate_write_target(
            source_path,
            code="PATCH_INVALID_PATH",
            path_str=operation.path,
        )
        return (
            {
                "type": "add_file",
                "path": str(source_path),
                "status": "applied",
                "hunks": 0,
            },
            {source_path: (_build_add_content(operation), None)},
        )

    if not source_path.exists():
        raise PatchError(
            "PATCH_TARGET_NOT_FOUND",
            f"Target file not found: {operation.path}",
            {"path": operation.path},
        )
    if not source_path.is_file():
        code = "PATCH_DELETE_NON_FILE" if operation.type == "delete" else "PATCH_UPDATE_NON_FILE"
        raise PatchError(
            code,
            f"Target path is not a regular file: {operation.path}",
            {"path": operation.path},
        )

    if operation.type == "delete":
        return (
            {
                "type": "delete_file",
                "path": str(source_path),
                "status": "applied",
                "hunks": 0,
            },
            {source_path: (None, None)},
        )

    current_content = _read_text_file(source_path, operation="apply_patch")
    final_content = _apply_hunks(current_content, operation)
    source_mode = source_path.stat().st_mode

    target_path = _resolve_patch_path(operation.move_to) if operation.move_to else source_path
    if operation.move_to:
        touched_paths.add(target_path)
        if target_path.exists():
            raise PatchError(
                "PATCH_MOVE_TARGET_EXISTS",
                f"Move target already exists: {operation.move_to}",
                {"path": operation.move_to},
            )
        _validate_write_target(
            target_path,
            code="PATCH_INVALID_PATH",
            path_str=operation.move_to or operation.path,
        )
        return (
            {
                "type": "move_file",
                "path": str(source_path),
                "new_path": str(target_path),
                "status": "applied",
                "hunks": len(operation.hunks),
            },
            {source_path: (None, None), target_path: (final_content, source_mode)},
        )

    return (
        {
            "type": "update_file",
            "path": str(source_path),
            "status": "applied",
            "hunks": len(operation.hunks),
        },
        {source_path: (final_content, source_mode)},
    )


def _build_snapshots(paths: Iterable[Path]) -> Dict[Path, SnapshotEntry]:
    snapshots: Dict[Path, SnapshotEntry] = {}
    for path in paths:
        if path.exists():
            snapshots[path] = SnapshotEntry(
                existed=True,
                content=_read_text_file(path, operation="rollback"),
                mode=path.stat().st_mode,
            )
        else:
            snapshots[path] = SnapshotEntry(existed=False, content=None, mode=None)
    return snapshots


def _rollback(snapshots: Dict[Path, SnapshotEntry]) -> None:
    for path, snapshot in snapshots.items():
        if snapshot.existed:
            assert snapshot.content is not None
            atomic_write_text(path, snapshot.content, mode=snapshot.mode)
        elif path.exists():
            path.unlink()


def execute_patch(document: PatchDocument, dry_run: bool = False) -> dict:
    """Validate, simulate, and optionally commit a patch document."""
    _validate_operation_graph(document)

    operations_result = []
    final_writes: Dict[Path, tuple[str | None, int | None]] = {}
    touched_paths: set[Path] = set()

    for operation in document.operations:
        result_item, writes = _simulate_operation(operation, touched_paths)
        operations_result.append(result_item)
        final_writes.update(writes)

    if dry_run:
        dry_run_operations = [
            {**operation_result, "status": "dry_run_ok"}
            for operation_result in operations_result
        ]
        return {
            "success": True,
            "dry_run": True,
            "summary": {
                "files_added": sum(op["type"] == "add_file" for op in operations_result),
                "files_updated": sum(op["type"] == "update_file" for op in operations_result),
                "files_deleted": sum(op["type"] == "delete_file" for op in operations_result),
                "files_moved": sum(op["type"] == "move_file" for op in operations_result),
                "hunks_applied": sum(op["hunks"] for op in operations_result),
            },
            "operations": dry_run_operations,
            "warnings": [],
        }

    snapshots = _build_snapshots(touched_paths)
    committed_paths: list[Path] = []
    try:
        for path, (content, mode) in final_writes.items():
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                atomic_write_text(path, content, mode=mode)
            committed_paths.append(path)
    except Exception as exc:
        try:
            _rollback({path: snapshots[path] for path in touched_paths})
        except Exception as rollback_exc:
            raise PatchError(
                "PATCH_INTERNAL_ERROR",
                "Patch failed and rollback was unsuccessful",
                {"error": str(exc), "rollback_error": str(rollback_exc)},
            ) from rollback_exc
        raise PatchError(
            "PATCH_INTERNAL_ERROR",
            "Patch commit failed and changes were rolled back",
            {"error": str(exc)},
        ) from exc

    return {
        "success": True,
        "dry_run": False,
        "summary": {
            "files_added": sum(op["type"] == "add_file" for op in operations_result),
            "files_updated": sum(op["type"] == "update_file" for op in operations_result),
            "files_deleted": sum(op["type"] == "delete_file" for op in operations_result),
            "files_moved": sum(op["type"] == "move_file" for op in operations_result),
            "hunks_applied": sum(op["hunks"] for op in operations_result),
        },
        "operations": operations_result,
        "warnings": [],
    }
