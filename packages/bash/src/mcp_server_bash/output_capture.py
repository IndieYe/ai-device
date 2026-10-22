"""Output capture utilities."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
import os
from pathlib import Path
import re
import tempfile
import time
import uuid
from typing import Deque, Optional, Tuple

OUTPUT_FILENAME_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}\.log$")

ANSI_ESCAPE_RE = re.compile(
    r"""
    \x1B
    (?:
        [@-Z\\-_]
        |\[[0-?]*[ -/]*[@-~]
        |\][^\x07]*(?:\x07|\x1B\\)
    )
    """,
    re.VERBOSE,
)


def sanitize_output_text(text: str) -> str:
    if not text:
        return text
    text = ANSI_ESCAPE_RE.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


class HeadBuffer:
    """Keep the head of a byte stream without splitting UTF-8 on decode."""

    def __init__(self, max_bytes: int, enabled: bool = True):
        self.max_bytes = max_bytes
        self.enabled = enabled
        self.capturing = True
        self.chunks: Deque[bytes] = deque()
        self.size = 0
        self.total = 0

    def append(self, chunk: bytes) -> None:
        if not self.capturing:
            return
        self.total += len(chunk)
        if not self.enabled or self.size >= self.max_bytes:
            return

        remaining = self.max_bytes - self.size
        if remaining <= 0:
            return

        kept = chunk[:remaining]
        if kept:
            self.chunks.append(kept)
            self.size += len(kept)

    @property
    def truncated(self) -> bool:
        return self.enabled and self.total > self.max_bytes

    def text(self) -> str:
        data = b"".join(self.chunks)
        if data and self.truncated:
            end = len(data)
            while end > 0 and (data[end - 1] & 0xC0) == 0x80:
                end -= 1
            data = data[:end]
        text = data.decode("utf-8", errors="replace") if data else ""
        return sanitize_output_text(text)

    def stop_capture(self) -> None:
        self.capturing = False
        self.chunks.clear()
        self.size = 0


class OutputLimitGuard:
    """Enforce a hard byte cap on the on-disk output file of one execution.

    The guard is shared with the caller through ``exceeded`` so that the command
    can be stopped as soon as the cap is crossed, instead of only when it exits.
    """

    def __init__(self, limit_bytes: Optional[int] = None):
        self.limit_bytes = limit_bytes
        self.written = 0
        self.exceeded = asyncio.Event()
        self.failed = asyncio.Event()

    def accept(self, size: int) -> bool:
        """Account for ``size`` bytes; return False once the cap would be crossed."""
        if self.exceeded.is_set():
            return False
        if self.limit_bytes is None:
            self.written += size
            return True
        if self.written + size > self.limit_bytes:
            return False
        self.written += size
        return True


def default_output_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "mcp-server-bash", "outputs")


def create_output_path(output_dir: str) -> str:
    expanded_dir = os.path.abspath(
        os.path.expandvars(os.path.expanduser(output_dir))
    )
    Path(expanded_dir).mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.log"
    path = Path(expanded_dir) / filename
    path.touch()
    return str(path)


async def read_stream(
    stream: asyncio.StreamReader,
    queue: asyncio.Queue[Optional[bytes]],
) -> None:
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        await queue.put(chunk)


def _persist_output(output_path: str, chunk: bytes, *, truncate: bool = False) -> None:
    # Each worker owns the complete file lifetime; no handle crosses cancellation.
    with open(output_path, "ab") as stream:
        if truncate:
            stream.truncate(0)
        else:
            stream.write(chunk)
        stream.flush()


async def _complete_output_io(output_path: str, chunk: bytes, *, truncate: bool = False) -> None:
    worker = asyncio.create_task(
        asyncio.to_thread(_persist_output, output_path, chunk, truncate=truncate)
    )
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Threads cannot be cancelled. Finish the write/clear/close before callers
        # reap a command or release ownership of the output file.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        if not worker.cancelled():
            worker.exception()
        raise


async def write_output(
    queue: asyncio.Queue[Optional[bytes]],
    output_path: str,
    head: HeadBuffer,
    guard: Optional[OutputLimitGuard] = None,
) -> None:
    failure = None
    try:
        await _complete_output_io(output_path, b"")
    except OSError as exc:
        failure = exc
        if guard is not None:
            guard.failed.set()
    while True:
        chunk = await queue.get()
        try:
            if chunk is None:
                if failure is not None:
                    raise failure
                return
            if failure is None:
                try:
                    if guard is None or guard.accept(len(chunk)):
                        await _complete_output_io(output_path, chunk)
                    elif not guard.exceeded.is_set():
                        try:
                            await _complete_output_io(output_path, b"", truncate=True)
                        finally:
                            guard.exceeded.set()
                except OSError as exc:
                    # Stop persisting but drain pipes until the owned producer
                    # exits. A failed writer must not strand a bounded queue.
                    failure = exc
                    if guard is not None:
                        guard.failed.set()
            head.append(chunk)
        finally:
            queue.task_done()


def cleanup_expired_outputs(
    output_dir: str,
    ttl_seconds: int,
    *,
    now: float | None = None,
) -> int:
    """Delete output files older than ``ttl_seconds``; return how many were removed."""
    root = _resolved_output_root(output_dir)
    if not root.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - ttl_seconds
    removed = 0
    for path in root.iterdir():
        if path.is_symlink() or not path.is_file() or not OUTPUT_FILENAME_RE.fullmatch(path.name):
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
    return removed


def _resolved_output_root(output_dir: str) -> Path:
    return Path(output_dir).expanduser().resolve(strict=False)


async def finish_output_tasks(
    readers: Tuple[asyncio.Task, asyncio.Task],
    queue: asyncio.Queue[Optional[bytes]],
    writer: asyncio.Task,
) -> None:
    await asyncio.gather(*readers, return_exceptions=True)
    await queue.put(None)
    await writer


def compute_returned_output_bytes(output: str) -> int:
    return len(output.encode("utf-8"))


def get_total_bytes(output_path: Optional[str]) -> Optional[int]:
    if not output_path:
        return None
    try:
        return os.path.getsize(output_path)
    except OSError:
        return None


def read_preview_file(
    output_path: str, max_bytes: int, *, prefer_tail_when_small: bool = False
) -> Tuple[str, bool, int]:
    """Return a bounded head/tail preview; the complete stream stays on disk."""
    max_bytes = max(0, max_bytes)
    try:
        size = os.path.getsize(output_path)
        with open(output_path, "rb") as f:
            if size <= max_bytes:
                text = sanitize_output_text(f.read().decode("utf-8", errors="replace"))
                return text, False, size

            marker = b"\n...[output omitted]...\n"
            if max_bytes < len(marker) + 2:
                # Tiny budgets cannot fit a useful split and a visible separator.
                if prefer_tail_when_small and max_bytes:
                    f.seek(size - max_bytes)
                text = sanitize_output_text(
                    f.read(max_bytes).decode("utf-8", errors="ignore")
                )
                return text, True, size

            head_bytes = (max_bytes - len(marker) + 1) // 2
            tail_bytes = max_bytes - len(marker) - head_bytes
            head = f.read(head_bytes)
            f.seek(size - tail_bytes)
            tail = f.read(tail_bytes)
    except Exception:
        return "", False, 0

    text = (
        head.decode("utf-8", errors="ignore")
        + marker.decode()
        + tail.decode("utf-8", errors="ignore")
    )
    return sanitize_output_text(text), True, size
