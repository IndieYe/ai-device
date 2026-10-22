"""Data models for apply_patch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


PatchLineKind = Literal["context", "delete", "add"]
PatchOperationType = Literal["add", "delete", "update"]


@dataclass
class PatchLine:
    """A single line inside a hunk."""

    kind: PatchLineKind
    text: str


@dataclass
class PatchHunk:
    """A hunk within an update operation."""

    header: str | None
    lines: list[PatchLine] = field(default_factory=list)


@dataclass
class PatchOperation:
    """A file-level patch operation."""

    type: PatchOperationType
    path: str
    move_to: str | None = None
    hunks: list[PatchHunk] = field(default_factory=list)
    added_lines: list[str] = field(default_factory=list)


@dataclass
class PatchDocument:
    """A parsed patch document."""

    operations: list[PatchOperation] = field(default_factory=list)
