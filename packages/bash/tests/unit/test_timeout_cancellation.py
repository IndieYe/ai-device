"""Cancellation during timeout handover must not abandon test-owned resources."""

import asyncio
import shlex
import sys

import pytest

from mcp_server_bash import shell
from mcp_server_bash.artifacts import create_script_artifacts, create_stdin_artifact


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["queue_join", "background_yield", "before_drain_start"])
@pytest.mark.parametrize("mode", ["run", "script"])
@pytest.mark.parametrize("repeat_cancel", [False, True])
@pytest.mark.parametrize("stall_readers", [False, True])
async def test_timeout_handover_cancellation_reaps_owned_resources(runtime,
    monkeypatch, tmp_path, window, mode, repeat_cancel, stall_readers,
):
    reached = asyncio.Event()
    gate = asyncio.Event()
    cleanup_entered = asyncio.Event()
    cleanup_gate = asyncio.Event()
    executions = []
    processes = runtime._tracker.processes
    baseline_tasks = asyncio.all_tasks()
    original_start = shell._start_execution
    original_sleep = asyncio.sleep
    original_queue = asyncio.Queue
    original_create_task = asyncio.create_task
    original_terminate = shell._terminate_execution
    original_read_stream = shell.read_stream
    request_task = None
    execution_task = None

    async def start(request):
        nonlocal execution_task
        execution_task = asyncio.current_task()
        execution = await original_start(request)
        executions.append(execution)
        return execution

    class PausedQueue(original_queue):
        async def join(self):
            if window == "queue_join":
                reached.set()
                await gate.wait()
            return await super().join()

    async def sleep(delay, *args, **kwargs):
        # Handover occurs in the runtime-owned task; cancel its caller below.
        if window == "background_yield" and delay == 0 and asyncio.current_task() is execution_task:
            reached.set()
            await gate.wait()
        else:
            await original_sleep(delay, *args, **kwargs)

    def create_task(coroutine, *args, **kwargs):
        task = original_create_task(coroutine, *args, **kwargs)
        if window == "before_drain_start" and coroutine.cr_code.co_name == "background_drain":
            # Exercise cancellation before the newly created drain can run its finally.
            task.cancel()
            request_task.cancel()
            reached.set()
        return task

    async def terminate(request, execution):
        if repeat_cancel:
            cleanup_entered.set()
            await cleanup_gate.wait()
        await original_terminate(request, execution)

    async def read_stream(stream, queue):
        if stall_readers:
            # A blocked output reader must be retired by bounded teardown.
            await gate.wait()
        await original_read_stream(stream, queue)

    monkeypatch.setattr(shell, "read_stream", read_stream)
    if stall_readers:
        monkeypatch.setattr(shell, "OUTPUT_FINISH_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(shell, "_start_execution", start)
    monkeypatch.setattr(shell, "_terminate_execution", terminate)
    monkeypatch.setattr(asyncio, "create_task", create_task)
    monkeypatch.setattr(asyncio, "Queue", PausedQueue)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    artifact_dir = str(tmp_path / "artifacts")
    kwargs = dict(cwd=str(tmp_path), timeout=0.01, output_dir=str(tmp_path / "outputs"), artifact_dir=artifact_dir)
    argv = (sys.executable, "-c", "import time; time.sleep(30)")
    if mode == "run":
        stdin_path, artifacts = create_stdin_artifact(b"test input", artifact_dir)
        operation = runtime.run_argv(argv, stdin_path=stdin_path, artifacts=artifacts, **kwargs)
    else:
        script = ("exec " + shlex.join(argv) + "\n").encode()
        script_path, stdin_path, artifacts = create_script_artifacts(script, b"test input", artifact_dir)
        operation = runtime.run_script(script_path, stdin_path=stdin_path, artifacts=artifacts, **kwargs)
    request_task = asyncio.create_task(operation)

    try:
        await asyncio.wait_for(reached.wait(), timeout=3)
        assert executions and executions[0].process.returncode is None
        request_task.cancel()
        if repeat_cancel:
            await asyncio.wait_for(cleanup_entered.wait(), timeout=3)
            request_task.cancel()
            await original_sleep(0)
            assert not request_task.done(), "Cancellation must wait for shielded cleanup"
            cleanup_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(request_task, timeout=5)
        assert executions[0].process.returncode is not None
        assert not processes
        assert not any((tmp_path / "artifacts").iterdir())
        assert not (asyncio.all_tasks() - baseline_tasks)
    finally:
        # Red-phase failures must never leak a subprocess or task from this test.
        cleanup_gate.set()
        for execution in executions:
            if execution.process.returncode is None:
                shell.kill_local_process_group(execution.pid)
            await asyncio.wait_for(execution.process.wait(), timeout=3)
        remaining = asyncio.all_tasks() - baseline_tasks
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
