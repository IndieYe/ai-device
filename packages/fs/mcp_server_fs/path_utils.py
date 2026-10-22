"""Path-related helpers."""

from __future__ import annotations

from pathlib import Path


def normalize_path(path: Path) -> Path:
    """Return a normalized absolute path."""
    return path.resolve()


def validate_path(path_str: str) -> Path:
    """Expand and normalize a user-provided path."""
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return normalize_path(path)
