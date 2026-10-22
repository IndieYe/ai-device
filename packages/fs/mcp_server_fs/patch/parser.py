"""Parser for the apply_patch DSL."""

from __future__ import annotations

from .errors import PatchError
from .models import PatchDocument, PatchHunk, PatchLine, PatchOperation


def _parse_prefixed_path(line: str, prefix: str, line_number: int) -> str:
    if not line.startswith(prefix):
        raise PatchError(
            "PATCH_PARSE_ERROR",
            f"line {line_number}: expected '{prefix}'",
            {"line": line_number},
        )

    path = line[len(prefix) :].strip()
    if not path:
        raise PatchError(
            "PATCH_PARSE_ERROR",
            f"line {line_number}: missing path after '{prefix}'",
            {"line": line_number},
        )
    return path


def parse_patch(patch: str) -> PatchDocument:
    """Parse patch DSL text into a structured document."""
    if not patch.strip():
        raise PatchError("PATCH_EMPTY", "Patch content is empty", {})

    lines = patch.splitlines()
    if not lines or lines[0] != "*** Begin Patch":
        raise PatchError(
            "PATCH_PARSE_ERROR",
            "line 1: patch must start with '*** Begin Patch'",
            {"line": 1},
        )
    if len(lines) < 2 or lines[-1] != "*** End Patch":
        raise PatchError(
            "PATCH_PARSE_ERROR",
            f"line {len(lines)}: patch must end with '*** End Patch'",
            {"line": len(lines)},
        )

    document = PatchDocument()
    current_operation: PatchOperation | None = None
    current_hunk: PatchHunk | None = None

    def flush_hunk() -> None:
        nonlocal current_hunk, current_operation
        if current_hunk is not None:
            assert current_operation is not None
            if not current_hunk.lines:
                raise PatchError(
                    "PATCH_PARSE_ERROR",
                    "Update File hunk cannot be empty",
                    {"path": current_operation.path},
                )
            current_operation.hunks.append(current_hunk)
            current_hunk = None

    def flush_operation() -> None:
        nonlocal current_operation
        if current_operation is None:
            return
        flush_hunk()
        if current_operation.type == "update" and not current_operation.hunks:
            raise PatchError(
                "PATCH_PARSE_ERROR",
                "Update File block requires at least one hunk",
                {"path": current_operation.path},
            )
        document.operations.append(current_operation)
        current_operation = None

    for index, line in enumerate(lines[1:-1], start=2):
        if line.startswith("*** Add File: "):
            flush_operation()
            current_operation = PatchOperation(
                type="add",
                path=_parse_prefixed_path(line, "*** Add File: ", index),
            )
            continue

        if line.startswith("*** Delete File: "):
            flush_operation()
            current_operation = PatchOperation(
                type="delete",
                path=_parse_prefixed_path(line, "*** Delete File: ", index),
            )
            continue

        if line.startswith("*** Update File: "):
            flush_operation()
            current_operation = PatchOperation(
                type="update",
                path=_parse_prefixed_path(line, "*** Update File: ", index),
            )
            continue

        if line.startswith("*** Move to: "):
            if current_operation is None or current_operation.type != "update":
                raise PatchError(
                    "PATCH_PARSE_ERROR",
                    f"line {index}: '*** Move to' is only allowed after Update File",
                    {"line": index},
                )
            if current_operation.hunks or current_hunk is not None:
                raise PatchError(
                    "PATCH_PARSE_ERROR",
                    f"line {index}: '*** Move to' must appear before hunks",
                    {"line": index, "path": current_operation.path},
                )
            if current_operation.move_to is not None:
                raise PatchError(
                    "PATCH_PARSE_ERROR",
                    f"line {index}: duplicate '*** Move to' in Update File block",
                    {"line": index, "path": current_operation.path},
                )
            current_operation.move_to = _parse_prefixed_path(
                line, "*** Move to: ", index
            )
            continue

        if line.startswith("@@"):
            if current_operation is None or current_operation.type != "update":
                raise PatchError(
                    "PATCH_PARSE_ERROR",
                    f"line {index}: unexpected hunk header outside Update File block",
                    {"line": index},
                )
            flush_hunk()
            header = line[2:].strip() or None
            current_hunk = PatchHunk(header=header)
            continue

        if current_operation is None:
            raise PatchError(
                "PATCH_PARSE_ERROR",
                f"line {index}: expected file operation header",
                {"line": index},
            )

        if current_operation.type == "add":
            if not line.startswith("+"):
                raise PatchError(
                    "PATCH_ADD_CONTAINS_INVALID_LINE",
                    f"line {index}: Add File block only allows '+' lines",
                    {"line": index, "path": current_operation.path},
                )
            current_operation.added_lines.append(line[1:])
            continue

        if current_operation.type == "delete":
            raise PatchError(
                "PATCH_PARSE_ERROR",
                f"line {index}: Delete File block cannot contain body content",
                {"line": index, "path": current_operation.path},
            )

        if current_hunk is None:
            raise PatchError(
                "PATCH_PARSE_ERROR",
                f"line {index}: expected hunk header '@@' after Update File block",
                {"line": index, "path": current_operation.path},
            )

        if not line:
            raise PatchError(
                "PATCH_PARSE_ERROR",
                f"line {index}: hunk lines must start with ' ', '+' or '-'",
                {"line": index, "path": current_operation.path},
            )

        prefix = line[0]
        if prefix == " ":
            kind = "context"
        elif prefix == "-":
            kind = "delete"
        elif prefix == "+":
            kind = "add"
        else:
            raise PatchError(
                "PATCH_PARSE_ERROR",
                f"line {index}: hunk lines must start with ' ', '+' or '-'",
                {"line": index, "path": current_operation.path},
            )

        current_hunk.lines.append(PatchLine(kind=kind, text=line[1:]))

    flush_operation()

    if not document.operations:
        raise PatchError(
            "PATCH_PARSE_ERROR",
            "Patch does not contain any file operations",
            {},
        )

    return document
