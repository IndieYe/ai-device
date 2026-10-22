"""Exercise the public core on real, test-owned local processes (no MCP)."""

import asyncio
import sys
from pathlib import Path

import psutil
import pytest

import mcp_server_bash.shell as shell
from mcp_server_bash.core import (
    ExecutionRuntime,
    create_script_artifacts,
    create_stdin_artifact,
)
from mcp_server_bash.models import BackgroundProcess


def owner(root, **kwargs):
    return ExecutionRuntime(
        output_dir=str(root / "outputs"), artifact_dir=str(root / "artifacts"), **kwargs
    )


@pytest.mark.asyncio
async def test_argv_boundaries_and_env(runtime, tmp_path):
    argument = "$CORE_VALUE; * spaces ' quotes"
    result = await runtime.run_argv(
        (sys.executable, "-c", "import os,sys; print(sys.argv[1]); print(os.getenv('CORE_VALUE'))", argument),
        cwd=str(tmp_path), env_overrides={"CORE_VALUE": "local-env"},
    )
    assert result["code"] == "OK"
    assert result["output"] == argument + "\nlocal-env\n"
    assert result["tool_mode"] == "run"


@pytest.mark.asyncio
async def test_shell_expansion_pipeline_and_cwd(runtime, tmp_path):
    result = await runtime.run_command(
        'printf "%s" "$CORE_VALUE" | tr a-z A-Z; printf "\\n"; pwd',
        cwd=str(tmp_path), env_overrides={"CORE_VALUE": "shell"},
    )
    assert result["code"] == "OK"
    assert result["output"] == f"SHELL\n{tmp_path}\n"
    assert result["tool_mode"] == "bash"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["run", "script"])
async def test_stdin_and_managed_files(runtime, tmp_path, mode):
    stdin = "data; $(not-a-command)\nsecond line\n"
    if mode == "run":
        stdin_path, artifacts = create_stdin_artifact(stdin.encode(), runtime.artifact_dir)
        result = await runtime.run_argv(
            (sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"),
            cwd=str(tmp_path), stdin_path=stdin_path, artifacts=artifacts,
        )
    else:
        script_path, stdin_path, artifacts = create_script_artifacts(
            b"while IFS= read -r line; do printf '%s\\n' \"$line\"; done\n",
            stdin.encode(), runtime.artifact_dir,
        )
        result = await runtime.run_script(
            script_path, cwd=str(tmp_path), stdin_path=stdin_path, artifacts=artifacts,
        )
    assert result["code"] == "OK"
    assert result["output"] == stdin
    assert result["tool_mode"] == mode
    assert not list(Path(runtime.artifact_dir).iterdir())


@pytest.mark.asyncio
async def test_syntax_error_never_executes_and_cleans(runtime, tmp_path):
    marker = tmp_path / "not-executed"
    path, stdin_path, artifacts = create_script_artifacts(
        f"touch '{marker}'\nif then\n".encode(), b"input", runtime.artifact_dir
    )
    result = await runtime.run_script(
        path, cwd=str(tmp_path), stdin_path=stdin_path, artifacts=artifacts
    )
    assert result["code"] == "SCRIPT_SYNTAX_ERROR"
    assert not marker.exists()
    assert not list(Path(runtime.artifact_dir).iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("output_bytes", [0, 4])
async def test_preview_limit_retains_complete_file(runtime, tmp_path, output_bytes):
    result = await runtime.run_command(
        "printf abcdefgh", cwd=str(tmp_path), max_output_bytes=4,
        output_bytes=output_bytes,
    )
    assert result["code"] == "OK"
    assert result["output"] == "abcdefgh"[:output_bytes]
    assert result["returned_output_bytes"] == output_bytes
    assert result["total_bytes"] == 8 and result["truncated"]
    assert Path(result["output_path"]).read_text() == "abcdefgh"


@pytest.mark.asyncio
async def test_disk_output_limit(runtime, tmp_path):
    result = await runtime.run_argv(
        (sys.executable, "-c", "print('x' * 10000)"), cwd=str(tmp_path),
        max_output_file_bytes=100,
    )
    assert result["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert Path(result["output_path"]).read_bytes() == b""


@pytest.mark.asyncio
async def test_two_owners_background_results_and_close_isolation(tmp_path):
    first, second = owner(tmp_path / "one"), owner(tmp_path / "two")
    async with first, second:
        argv = (sys.executable, "-c", "import time; time.sleep(0.3); print('finished')")
        a = await first.run_argv(argv, cwd=str(tmp_path), timeout=0.01)
        b = await second.run_argv(argv, cwd=str(tmp_path), timeout=0.01)
        assert a["code"] == b["code"] == "BACKGROUND"
        pid_a, pid_b = a["background_pid"], b["background_pid"]
        assert (await second.get_background_result(pid_a))["code"] == "NOT_RUNNING"
        assert (await first.get_background_result(pid_b))["code"] == "NOT_RUNNING"
        assert (await second.get_background_result(pid_b, timeout=0.01))["code"] == "RUNNING"
        await first.aclose()
        assert not psutil.pid_exists(pid_a)
        assert not first._tracker.processes and not first._tracker._timers
        completed = await second.get_background_result(pid_b, timeout=2, output_bytes=200)
        assert completed["code"] == "OK" and completed["output"] == "finished\n"
        assert (await second.get_background_result(pid_b))["output"] == completed["output"]
        assert Path(a["output_path"]).exists()  # Output is caller-owned, not deleted on close.
    assert not second._tracker.processes and not second._tracker._timers


@pytest.mark.asyncio
async def test_close_cleans_background_script_and_rejects_reuse(runtime, tmp_path):
    path, stdin_path, artifacts = create_script_artifacts(
        b"sleep 30\n", b"input", runtime.artifact_dir
    )
    result = await runtime.run_script(
        path, cwd=str(tmp_path), stdin_path=stdin_path, artifacts=artifacts, timeout=0.01
    )
    assert result["code"] == "BACKGROUND"
    assert Path(path).exists()
    await runtime.aclose()
    await runtime.aclose()
    assert not psutil.pid_exists(result["background_pid"])
    assert not list(Path(runtime.artifact_dir).iterdir())
    with pytest.raises(RuntimeError, match="closed"):
        await runtime.run_command("true", cwd=str(tmp_path))
    with pytest.raises(RuntimeError, match="closed"):
        await runtime.get_background_result(result["background_pid"])


@pytest.mark.asyncio
async def test_close_cancels_active_foreground_execution(runtime, tmp_path, monkeypatch):
    started = asyncio.Event()
    executions = []
    original = shell._start_execution

    async def start(request):
        execution = await original(request)
        executions.append(execution)
        started.set()
        return execution

    monkeypatch.setattr(shell, "_start_execution", start)
    task = asyncio.create_task(runtime.run_argv(
        (sys.executable, "-c", "import time; time.sleep(30)"), cwd=str(tmp_path)
    ))
    await asyncio.wait_for(started.wait(), 3)
    await runtime.aclose()
    assert task.cancelled()
    assert not psutil.pid_exists(executions[0].pid)
    assert not runtime._active


@pytest.mark.asyncio
async def test_context_exception_cleans_owned_background(tmp_path):
    instance = owner(tmp_path)
    with pytest.raises(ValueError, match="caller error"):
        async with instance:
            result = await instance.run_argv(
                (sys.executable, "-c", "import time; time.sleep(30)"),
                cwd=str(tmp_path), timeout=0.01,
            )
            raise ValueError("caller error")
    assert not psutil.pid_exists(result["background_pid"])
    assert not instance._tracker.processes


@pytest.mark.asyncio
async def test_start_failure_cleans_stdin(runtime, tmp_path):
    path, artifacts = create_stdin_artifact(b"input", runtime.artifact_dir)
    result = await runtime.run_argv(
        (str(tmp_path / "missing-program"),), cwd=str(tmp_path),
        stdin_path=path, artifacts=artifacts,
    )
    assert result["code"] == "EXECUTION_ERROR" and not result["success"]
    assert not list(Path(runtime.artifact_dir).iterdir())
    assert not runtime._active and not runtime._tracker.processes


@pytest.mark.asyncio
async def test_timeout_kill_is_not_registered(runtime, tmp_path):
    result = await runtime.run_argv(
        (sys.executable, "-c", "import time; time.sleep(30)"), cwd=str(tmp_path),
        timeout=0.01, timeout_behavior="kill",
    )
    assert result["code"] == "TIMEOUT" and not runtime._tracker.processes


@pytest.mark.asyncio
async def test_result_retention_expires_without_deleting_output(tmp_path):
    async with owner(tmp_path, completed_process_retention_seconds=0.03) as instance:
        result = await instance.run_argv(
            (sys.executable, "-c", "import time; time.sleep(0.08); print('retained')"),
            cwd=str(tmp_path), timeout=0.01,
        )
        pid = result["background_pid"]
        assert (await instance.get_background_result(pid, timeout=2))["code"] == "OK"
        await asyncio.sleep(0.06)
        assert (await instance.get_background_result(pid))["code"] == "NOT_RUNNING"
        assert not instance._tracker._timers
        assert Path(result["output_path"]).read_text() == "retained\n"


@pytest.mark.asyncio
async def test_exceptional_drain_is_reported_and_close_clears_it(runtime, tmp_path):
    async def fail():
        raise OSError("test drain failure")

    task = asyncio.create_task(fail())
    process = BackgroundProcess(
        pid=12345, display_command="synthetic", output_path=str(tmp_path / "missing"),
        start_time=0, task=task, tool_mode="run",
    )
    runtime._tracker.register(process)
    await asyncio.sleep(0)
    result = await runtime.get_background_result(process.pid)
    assert result["code"] == "EXECUTION_ERROR"
    assert "test drain failure" in result["message"]
    await runtime.aclose()
    assert not runtime._tracker.processes and not runtime._tracker._timers


@pytest.mark.asyncio
async def test_output_capture_failure_cleans_managed_artifacts(runtime, tmp_path, monkeypatch):
    executions = []
    original = shell._start_execution

    async def start(request):
        result = await original(request)
        executions.append(result)
        return result

    async def broken_writer(*args):
        raise OSError("test output failure")

    monkeypatch.setattr(shell, "_start_execution", start)
    monkeypatch.setattr(shell, "write_output", broken_writer)
    path, artifacts = create_stdin_artifact(b"input", runtime.artifact_dir)
    with pytest.raises(OSError, match="test output failure"):
        await runtime.run_argv(
            (sys.executable, "-c", "print('output')"), cwd=str(tmp_path),
            stdin_path=path, artifacts=artifacts,
        )
    assert not psutil.pid_exists(executions[0].pid)
    assert not list(Path(runtime.artifact_dir).iterdir())
    assert not runtime._active


@pytest.mark.asyncio
async def test_close_survives_repeated_cancellation(runtime, tmp_path, monkeypatch):
    result = await runtime.run_argv(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=str(tmp_path), timeout=0.01,
    )
    reached, gate = asyncio.Event(), asyncio.Event()
    original = shell._terminate_execution

    async def paused_terminate(request, execution):
        reached.set()
        await gate.wait()
        await original(request, execution)

    monkeypatch.setattr(shell, "_terminate_execution", paused_terminate)
    closing = asyncio.create_task(runtime.aclose())
    await asyncio.wait_for(reached.wait(), 3)
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await runtime.aclose()
    assert not psutil.pid_exists(result["background_pid"])
    assert not runtime._tracker.processes and not runtime._tracker._timers
