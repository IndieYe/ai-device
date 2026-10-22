"""I/O helpers for temp files and atomic writes."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

def save_to_temp_file(
    content: str, prefix: str = "result", *, temp_dir: str | Path | None = None,
) -> str:
    """Save content to a temp file and return its absolute path."""
    content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
    directory = Path(temp_dir) if temp_dir is not None else Path(tempfile.gettempdir()) / "mcp-server-fs"
    file_path = directory / f"{prefix}_{content_hash}.txt"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return str(file_path)


def atomic_write_text(file_path: Path, content: str, mode: int | None = None) -> None:
    """Atomically write text content to disk."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None and file_path.exists():
        mode = stat.S_IMODE(file_path.stat().st_mode)
    temp_fd, temp_path = tempfile.mkstemp(
        dir=file_path.parent,
        prefix=".tmp_",
        suffix=".txt",
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as file_obj:
            file_obj.write(content)
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, file_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        raise
