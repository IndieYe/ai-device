"""An already-exited child must not turn an output-budget failure into success."""

import asyncio
import sys

import pytest

from mcp_server_bash import shell


@pytest.mark.asyncio
async def test_output_limit_after_zero_exit_is_still_failure(
    runtime, tmp_path, monkeypatch
):
    original = shell._stop_on_output_limit

    async def delayed(*args):
        # Force child exit before the budget watcher can terminate it. The writer
        # still enforces/clears the file; terminal status must observe its guard.
        await asyncio.sleep(0.2)
        await original(*args)

    monkeypatch.setattr(shell, "_stop_on_output_limit", delayed)
    result = await runtime.run_argv(
        (
            sys.executable,
            "-c",
            "import os,time; time.sleep(.05); os.write(1,b'x'*2048)",
        ),
        cwd=str(tmp_path),
        timeout=0.001,
        timeout_behavior="background",
        max_output_file_bytes=1024,
    )
    final = await runtime.get_background_result(
        result["background_pid"], timeout=2, output_bytes=100
    )
    assert not final["success"] and final["exit_code"] == -1
    assert final["total_bytes"] == 0


@pytest.mark.asyncio
async def test_failed_disk_writer_stops_producer_and_retires_bounded_readers(
    runtime, tmp_path, monkeypatch
):
    from mcp_server_bash import output_capture as capture

    original = capture._persist_output

    def fail(path, chunk, *, truncate=False):
        if chunk:
            raise OSError("test-only output write failure")
        return original(path, chunk, truncate=truncate)

    monkeypatch.setattr(capture, "_persist_output", fail)
    result = await runtime.run_argv(
        (
            sys.executable,
            "-c",
            "import os,time; time.sleep(.05)\nwhile True: os.write(1,b'x'*8192)",
        ),
        cwd=str(tmp_path),
        timeout=0.001,
        timeout_behavior="background",
    )
    async with asyncio.timeout(3):
        final = await runtime.get_background_result(
            result["background_pid"], timeout=2, output_bytes=100
        )
    assert not final["success"] and final["code"] == "EXECUTION_ERROR", final
