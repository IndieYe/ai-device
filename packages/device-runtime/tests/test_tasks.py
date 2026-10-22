"""Task-core lifecycle, output budgets, cancellation and restart fault regressions."""

import asyncio
import sys
from pathlib import Path

import pytest

from device_runtime.executor import LocalExecutor
from device_runtime.protocol import Operation


def operation(action, arguments, op_id="task-start"):
    if action == "bash.execute":
        arguments = {"mode": "run", "timeout": 0.001, **arguments}
    return Operation(device_id="laptop-one", operation_id=op_id, action=action, arguments=arguments)


async def call(executor, action, args, op_id="task-query"):
    state, result = await executor(operation(action, args, op_id))
    assert state == ("succeeded" if result["data"]["success"] else "failed"), result
    assert result["location"] == {"namespace": "device", "device_id": "laptop-one"}
    return result["data"]


async def terminal(executor, task_id):
    async with asyncio.timeout(5):
        while True:
            data = await call(executor, "bash.get", {"execution_id": task_id})
            if data["state"] != "running":
                return data
            await asyncio.sleep(0.02)


async def test_background_id_dedup_and_tail_output(tmp_path):
    state = tmp_path / "runtime"
    file = tmp_path / "once.txt"
    executor = LocalExecutor(state, "laptop-one")
    args = {
        "argv": [
            sys.executable,
            "-c",
            "import pathlib,sys,time; pathlib.Path(sys.argv[1]).open('a').write('once\\n'); print('中文',flush=True); time.sleep(.2)",
            str(file),
        ],
        "cwd": str(tmp_path),
    }
    try:
        started = await call(executor, "bash.execute", args, "one")
        assert started["execution_id"] == "one"
        await call(executor, "bash.execute", args, "one")
        result = await terminal(executor, "one")
        assert result["state"] == "succeeded" and result["exit_code"] == 0
        assert file.read_text() == "once\n"
        queried = await call(executor, "bash.get", {"execution_id": "one", "bytes": 1000})
        assert queried["output"] == "中文\n"
    finally:
        await executor.aclose()
    recovered = LocalExecutor(state, "laptop-one")
    try:
        result = await call(recovered, "bash.get", {"execution_id": "one"})
        assert result["state"] == "succeeded"
    finally:
        await recovered.aclose()


async def test_background_nonzero_exit_is_task_failed(tmp_path):
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    try:
        await call(
            executor,
            "bash.execute",
            {"argv": [sys.executable, "-c", "raise SystemExit(7)"], "cwd": str(tmp_path)},
            "bad",
        )
        result = await terminal(executor, "bad")
        assert result["state"] == "failed" and result["exit_code"] == 7
    finally:
        await executor.aclose()


async def test_unfinished_task_restart_is_unknown_without_replay(tmp_path):
    state = tmp_path / "runtime"
    executor = LocalExecutor(state, "laptop-one")
    args = {"argv": [sys.executable, "-c", "import time; time.sleep(10)"], "cwd": str(tmp_path)}
    try:
        started = await call(executor, "bash.execute", args, "uncertain")
        assert started["state"] == "running"
        assert started["output_path"] is not None
    finally:
        await executor.aclose()
    recovered = LocalExecutor(state, "laptop-one")
    try:
        result = await call(recovered, "bash.get", {"execution_id": "uncertain"})
        assert result["state"] == "unknown"
        assert result["output_path"] == started["output_path"]
        repeated = await call(recovered, "bash.execute", args, "uncertain")
        assert repeated["state"] == "unknown"
        assert not recovered.tasks._monitors
    finally:
        await recovered.aclose()


@pytest.mark.parametrize("wait_after_output", [0, 10])
async def test_output_cap_stops_owned_command_and_bounds_file(tmp_path, wait_after_output):
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    try:
        await call(
            executor,
            "bash.execute",
            {
                "argv": [
                    sys.executable,
                    "-c",
                    f"import os,time; time.sleep(.1); os.write(1,b'x'*200000); time.sleep({wait_after_output})",
                ],
                "cwd": str(tmp_path),
                "output_limit_bytes": 1024,
            },
            "cap",
        )
        task = await terminal(executor, "cap")
        assert task["state"] == "failed" and task["exit_code"] != 0
        assert Path(task["output_path"]).stat().st_size <= 1024
    finally:
        await executor.aclose()


async def test_stdin_survives_start_receipt_and_combined_streams(tmp_path):
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    try:
        await call(
            executor,
            "bash.execute",
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "import sys,time; time.sleep(.1); sys.stdout.write(sys.stdin.read()); sys.stdout.flush(); sys.stderr.write('err\\n')",
                ],
                "cwd": str(tmp_path),
                "stdin": "中文\\n".replace("\\n", "\n"),
            },
            "stdin",
        )
        assert (await terminal(executor, "stdin"))["state"] == "succeeded"
        output = await call(executor, "bash.get", {"execution_id": "stdin", "bytes": 1000})
        assert "中文\n" in output["output"] and "err\n" in output["output"]
    finally:
        await executor.aclose()


async def test_concurrent_capacity_and_missing_task(tmp_path, monkeypatch):
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one", max_active_tasks=2)
    args = {"argv": [sys.executable, "-c", "import time; time.sleep(10)"], "cwd": str(tmp_path)}
    try:
        results = await asyncio.gather(
            *(executor(operation("bash.execute", args, f"capacity-{n}")) for n in range(3))
        )
        assert [state for state, _ in results].count("succeeded") == 2
        rejected = next(result for state, result in results if state == "failed")
        assert rejected["data"]["code"] == "TASK_CAPACITY_EXCEEDED"
        assert len(executor.tasks._monitors) == 2
        missing = await executor(operation("bash.get", {"execution_id": "missing"}))
        assert missing[0] == "failed" and missing[1]["data"]["code"] == "EXECUTION_NOT_FOUND"
    finally:
        await executor.aclose()


async def test_start_cancellation_and_monitor_failure_are_unknown(tmp_path, monkeypatch):
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    ready = asyncio.Event()
    calls = 0

    async def paused(*args, **kwargs):
        nonlocal calls
        calls += 1
        ready.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(executor.tasks, "run_command", paused)
    args = {"argv": [sys.executable, "-c", "pass"], "cwd": str(tmp_path)}
    try:
        start = asyncio.create_task(executor(operation("bash.execute", args, "cancelled")))
        await ready.wait()
        start.cancel()
        import pytest

        with pytest.raises(asyncio.CancelledError):
            await start
        assert (await call(executor, "bash.execute", args, "cancelled"))["state"] == "unknown"
        assert calls == 1
        monkeypatch.setattr(executor.tasks, "run_command", executor._run_command)

        async def broken(*args, **kwargs):
            raise OSError("tracking failed")

        monkeypatch.setattr(executor.runtime, "get_background_result", broken)
        await call(
            executor,
            "bash.execute",
            {**args, "argv": [sys.executable, "-c", "import time; time.sleep(10)"]},
            "lost",
        )
        task = await terminal(executor, "lost")
        assert task["state"] == "unknown" and task["output_path"]
    finally:
        await executor.aclose()


async def test_durable_intent_without_monitor_is_persistently_unknown(tmp_path):
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    op = operation(
        "bash.execute", {"argv": [sys.executable, "-c", "pass"], "cwd": str(tmp_path)}, "intent"
    )
    try:
        await executor.tasks.journal.initialize()
        await executor.tasks.journal.accept(op, "running")
        repeated = await call(executor, "bash.execute", op.arguments, "intent")
        assert repeated["state"] == "unknown"
        assert (await executor.tasks.journal.get("intent"))["state"] == "unknown"
        assert not executor.tasks._monitors
    finally:
        await executor.aclose()


async def test_abrupt_device_process_exit_retains_unknown_no_replay(tmp_path):
    import json
    import os
    from pathlib import Path

    state_dir, marker = tmp_path / "runtime", tmp_path / "once"
    args = {
        "argv": [
            sys.executable,
            "-c",
            "import pathlib,sys,time; pathlib.Path(sys.argv[1]).open('a').write('once\\n'); time.sleep(.5)",
            str(marker),
        ],
        "cwd": str(tmp_path),
    }
    source = """
import asyncio,json,os,sys
from pathlib import Path
from device_runtime.executor import LocalExecutor
from device_runtime.protocol import Operation
async def main():
    executor=LocalExecutor(Path(sys.argv[1]), 'laptop-one')
    await executor(Operation(device_id='laptop-one', operation_id='crash', action='bash.execute', arguments={"mode":"run","timeout":0.001,**json.loads(sys.argv[2])}))
    os._exit(0)
asyncio.run(main())
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", source, str(state_dir), json.dumps(args), env=env
    )
    assert await asyncio.wait_for(proc.wait(), 5) == 0
    async with asyncio.timeout(3):
        while not marker.exists():
            await asyncio.sleep(0.01)
    recovered = LocalExecutor(state_dir, "laptop-one")
    try:
        result = await call(recovered, "bash.get", {"execution_id": "crash"})
        assert result["state"] == "unknown" and result["output_path"]
        await call(recovered, "bash.execute", args, "crash")
        await asyncio.sleep(0.6)  # The short isolated orphan finishes by itself; no PID adoption.
        assert marker.read_text() == "once\n"
        assert not recovered.tasks._monitors
    finally:
        await recovered.aclose()


async def test_cancel_after_spawn_keeps_output_and_capacity_until_exit(tmp_path, monkeypatch):
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one", max_active_tasks=1)
    waiting = asyncio.Event()
    original = executor.tasks.journal.update

    async def pause_persist(task_id, state, *args, **kwargs):
        if state == "running":
            waiting.set()
            await asyncio.Event().wait()
        return await original(task_id, state, *args, **kwargs)

    monkeypatch.setattr(executor.tasks.journal, "update", pause_persist)
    args = {
        "cwd": str(tmp_path),
        "argv": [sys.executable, "-c", "import time; print('alive',flush=True); time.sleep(10)"],
    }
    start = asyncio.create_task(executor(operation("bash.execute", args, "cancel-after-spawn")))
    try:
        await asyncio.wait_for(waiting.wait(), 3)
        start.cancel()
        import pytest

        with pytest.raises(asyncio.CancelledError):
            await start
        result = await call(executor, "bash.get", {"execution_id": "cancel-after-spawn"})
        assert result["state"] == "unknown" and result["output_path"]
        assert len(executor.tasks._monitors) == 1
        state, result = await executor(operation("bash.execute", args, "another-task"))
        assert state == "failed" and result["data"]["code"] == "TASK_CAPACITY_EXCEEDED"
    finally:
        await executor.aclose()
    recovered = LocalExecutor(tmp_path / "runtime", "laptop-one")
    try:
        result = await call(recovered, "bash.get", {"execution_id": "cancel-after-spawn"})
        assert result["state"] == "unknown" and result["output_path"]
    finally:
        await recovered.aclose()


async def test_monitor_failure_does_not_release_owned_execution_capacity(tmp_path, monkeypatch):
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one", max_active_tasks=1)

    async def broken(*args, **kwargs):
        raise OSError("monitor failed while command remains active")

    monkeypatch.setattr(executor.runtime, "get_background_result", broken)
    args = {"argv": [sys.executable, "-c", "import time; time.sleep(10)"], "cwd": str(tmp_path)}
    try:
        await call(executor, "bash.execute", args, "owned")
        assert (await terminal(executor, "owned"))["state"] == "unknown"
        async with asyncio.timeout(1):
            while executor.tasks._monitors:
                await asyncio.sleep(0.01)
        assert executor.tasks._owned == {"owned"}
        for n in range(3):
            state, result = await executor(operation("bash.execute", args, f"blocked-{n}"))
            assert state == "failed" and result["data"]["code"] == "TASK_CAPACITY_EXCEEDED"
    finally:
        await executor.aclose()
    assert not executor.tasks._owned


async def test_device_disk_failure_is_unknown_with_output_not_permanently_running(
    tmp_path, monkeypatch
):
    from mcp_server_bash import output_capture as capture

    original = capture._persist_output

    def fail(path, chunk, *, truncate=False):
        if chunk:
            raise OSError("test-only disk write failure")
        return original(path, chunk, truncate=truncate)

    monkeypatch.setattr(capture, "_persist_output", fail)
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    try:
        await call(
            executor,
            "bash.execute",
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "import os,time; time.sleep(.05)\nwhile True: os.write(1,b'x'*8192)",
                ],
                "cwd": str(tmp_path),
            },
            "disk-failure",
        )
        task = await terminal(executor, "disk-failure")
        assert task["state"] == "unknown" and task["output_path"]
        # The core reported an execution error, not a verified exit code. Keep
        # the slot conservatively until owned-runtime cleanup confirms closure.
        assert executor.tasks._owned == {"disk-failure"}
    finally:
        await executor.aclose()
    assert not executor.tasks._owned
