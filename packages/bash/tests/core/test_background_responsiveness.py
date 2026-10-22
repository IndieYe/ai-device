"""Slow result-file access must not block unrelated event-loop work."""

import asyncio
import sys
import threading

import pytest

from mcp_server_bash import tracking


@pytest.mark.asyncio
@pytest.mark.parametrize("finished", [False, True])
async def test_slow_background_preview_keeps_loop_responsive(
    runtime, tmp_path, monkeypatch, finished
):
    result = await runtime.run_argv(
        (sys.executable, "-c", "import time; print('ok',flush=True); time.sleep(.2)"),
        cwd=str(tmp_path),
        timeout=0.001,
        timeout_behavior="background",
    )
    pid = result["background_pid"]
    if finished:
        await asyncio.sleep(0.3)
    entered, release = threading.Event(), threading.Event()
    original = tracking.read_preview_file

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(tracking, "read_preview_file", slow)
    query = asyncio.create_task(
        runtime.get_background_result(pid, timeout=0, output_bytes=100)
    )
    try:
        # A worker can enter while this coroutine keeps running. On the old
        # synchronous implementation, release cannot run before slow() times out.
        async with asyncio.timeout(1):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        await asyncio.sleep(0.01)
        assert not query.done()
    finally:
        release.set()
        await query


@pytest.mark.asyncio
async def test_slow_background_handoff_preview_keeps_loop_responsive(
    runtime, tmp_path, monkeypatch
):
    from mcp_server_bash import shell

    entered, release = threading.Event(), threading.Event()
    original = shell.read_preview_file

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(shell, "read_preview_file", slow)
    start = asyncio.create_task(
        runtime.run_argv(
            (sys.executable, "-c", "import time; time.sleep(.2)"),
            cwd=str(tmp_path),
            timeout=0.001,
            timeout_behavior="background",
        )
    )
    try:
        async with asyncio.timeout(1):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        await asyncio.sleep(0.01)
        assert not start.done()
    finally:
        release.set()
        result = await start
        assert result["code"] == "BACKGROUND"


@pytest.mark.asyncio
@pytest.mark.parametrize("truncate", [False, True])
async def test_slow_output_write_and_clear_keep_loop_responsive(
    tmp_path, monkeypatch, truncate
):
    from mcp_server_bash import output_capture as capture

    entered, release = threading.Event(), threading.Event()
    original = capture._persist_output

    def slow(path, chunk, *, truncate=False):
        if chunk or truncate:
            entered.set()
            assert release.wait(2)
        return original(path, chunk, truncate=truncate)

    monkeypatch.setattr(capture, "_persist_output", slow)
    path = tmp_path / "output"
    queue = asyncio.Queue()
    for chunk in (b"abc", None):
        await queue.put(chunk)
    guard = capture.OutputLimitGuard(1 if truncate else 100)
    writer = asyncio.create_task(
        capture.write_output(queue, str(path), capture.HeadBuffer(100), guard)
    )
    try:
        async with asyncio.timeout(1):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        writer.cancel()
        await asyncio.sleep(0.01)
        assert not writer.done()  # Cancellation waits for in-flight file I/O.
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await writer
    assert path.read_bytes() == (b"" if truncate else b"abc")
    assert guard.exceeded.is_set() == truncate


@pytest.mark.asyncio
async def test_output_stays_empty_after_overflow(tmp_path):
    from mcp_server_bash.output_capture import (
        HeadBuffer,
        OutputLimitGuard,
        write_output,
    )

    path = tmp_path / "output"
    queue = asyncio.Queue()
    for chunk in (b"overflow", b"x", None):
        await queue.put(chunk)
    guard = OutputLimitGuard(2)
    await write_output(queue, str(path), HeadBuffer(100), guard)
    await queue.join()
    assert guard.exceeded.is_set() and path.read_bytes() == b""
