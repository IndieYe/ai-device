"""Managed temporary resources for command execution."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

from .models import ExecutionArtifacts

EXECUTION_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def default_artifact_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "mcp-server-bash", "artifacts")


def create_stdin_artifact(content: bytes, artifact_dir: str) -> tuple[str, ExecutionArtifacts]:
    paths, artifacts = _create_execution_artifacts(
        artifact_dir,
        (("stdin.bin", content),),
    )
    return paths[0], artifacts


def create_script_artifacts(
    script: bytes,
    stdin: bytes | None,
    artifact_dir: str,
) -> tuple[str, str | None, ExecutionArtifacts]:
    files = [("script.sh", script)]
    if stdin is not None:
        files.append(("stdin.bin", stdin))
    paths, artifacts = _create_execution_artifacts(artifact_dir, tuple(files))
    return paths[0], paths[1] if stdin is not None else None, artifacts


def _create_execution_artifacts(
    artifact_dir: str,
    files: tuple[tuple[str, bytes], ...],
) -> tuple[tuple[str, ...], ExecutionArtifacts]:
    root = _resolved_root(artifact_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    execution_dir = root / uuid.uuid4().hex
    execution_dir.mkdir(mode=0o700)
    paths: list[str] = []
    try:
        for filename, content in files:
            path = execution_dir / filename
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            paths.append(str(path))
    except BaseException:
        shutil.rmtree(execution_dir, ignore_errors=True)
        raise
    return tuple(paths), ExecutionArtifacts(local_paths=(str(execution_dir),))


def cleanup_artifacts(artifacts: ExecutionArtifacts | None, artifact_dir: str) -> None:
    if artifacts is None:
        return
    root = _resolved_root(artifact_dir)
    for raw_path in artifacts.local_paths:
        path = Path(raw_path).expanduser().resolve(strict=False)
        if path.parent != root or not EXECUTION_ID_RE.fullmatch(path.name):
            continue
        shutil.rmtree(path, ignore_errors=True)


def cleanup_expired_artifacts(
    artifact_dir: str,
    ttl_seconds: int,
    *,
    now: float | None = None,
) -> int:
    root = _resolved_root(artifact_dir)
    if not root.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - ttl_seconds
    removed = 0
    for path in root.iterdir():
        if path.is_symlink() or not path.is_dir() or not EXECUTION_ID_RE.fullmatch(path.name):
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(path)
            removed += 1
        except FileNotFoundError:
            continue
    return removed


def _resolved_root(artifact_dir: str) -> Path:
    return Path(artifact_dir).expanduser().resolve(strict=False)
