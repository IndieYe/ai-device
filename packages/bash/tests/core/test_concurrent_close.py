"""External close must not wait for callers re-entering the same close.

Use an isolated interpreter with only in-memory execution. A regression is
bounded by the parent timeout without leaving real commands or process trees.
"""

import json
import subprocess
import sys

import pytest

PROBE = r"""
import asyncio
import json
import sys
from mcp_server_bash.core import ExecutionRuntime

async def main():
    runtime = ExecutionRuntime()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def execution(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    runtime._run_execution = execution

    async def caller():
        if sys.argv[1] == "context":
            async with runtime:
                await runtime.run_command("unused", cwd=".")
        else:
            try:
                await runtime.run_command("unused", cwd=".")
            finally:
                await runtime.aclose()

    owner = asyncio.create_task(caller())
    await started.wait()
    closer = asyncio.create_task(runtime.aclose())
    outcomes = await asyncio.gather(owner, closer, return_exceptions=True)
    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert outcomes[1] is None
    assert stopped.is_set()
    assert runtime._tracker._closed
    assert not runtime._active
    assert not runtime._tracker.processes and not runtime._tracker._timers
    await runtime.aclose()
    print(json.dumps({"caller_cancelled": owner.cancelled(), "close_completed": closer.done(), "tracker_closed": runtime._tracker._closed}))

asyncio.run(main())
"""


@pytest.mark.parametrize("exit_style", ["context", "finally"])
def test_external_close_and_caller_cleanup_do_not_deadlock(tmp_path, exit_style):
    completed = subprocess.run(
        [sys.executable, "-I", "-c", PROBE, exit_style],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "caller_cancelled": True,
        "close_completed": True,
        "tracker_closed": True,
    }
