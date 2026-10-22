"""Accepted managed files must be cleaned even if the owned Task never starts."""

import asyncio
import sys
from pathlib import Path

import pytest

from mcp_server_bash.artifacts import create_script_artifacts, create_stdin_artifact


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["run", "script"])
@pytest.mark.parametrize("directory_mode", ["instance", "override"])
@pytest.mark.parametrize("cancel_target", ["caller", "owned"])
async def test_prestart_cancellation_cleans_accepted_artifacts(
    runtime,
    tmp_path,
    monkeypatch,
    mode,
    directory_mode,
    cancel_target,
):
    directory = (
        runtime.artifact_dir
        if directory_mode == "instance"
        else str(tmp_path / "override")
    )
    if mode == "run":
        stdin_path, artifacts = create_stdin_artifact(b"input", directory)
        script_path = None
    else:
        script_path, stdin_path, artifacts = create_script_artifacts(
            b"cat\n", b"input", directory
        )
    preserved = tmp_path / "caller-owned.txt"
    preserved.write_text("keep")
    started = False

    async def execution_must_not_start(*args, **kwargs):
        nonlocal started
        started = True
        raise AssertionError("owned execution unexpectedly started")

    monkeypatch.setattr(runtime, "_run_execution", execution_must_not_start)
    original_create_task = asyncio.create_task
    created = []

    def cancel_before_first_step(coroutine, *args, **kwargs):
        task = original_create_task(coroutine, *args, **kwargs)
        if coroutine.cr_code.co_name in {
            "_run_execution",
            "owned_execution",
            "execution_must_not_start",
        }:
            created.append(task)
            if cancel_target == "caller":
                asyncio.current_task().cancel()
            else:
                task.cancel()
        return task

    # Restore the creation hook before fixture shutdown, so only submission is affected.
    with monkeypatch.context() as scoped:
        scoped.setattr(asyncio, "create_task", cancel_before_first_step)
        with pytest.raises(asyncio.CancelledError):
            kwargs = dict(cwd=str(tmp_path), stdin_path=stdin_path, artifacts=artifacts)
            if directory_mode == "override":
                kwargs["artifact_dir"] = directory
            if mode == "run":
                await runtime.run_argv((sys.executable, "-c", "pass"), **kwargs)
            else:
                await runtime.run_script(script_path, **kwargs)

    assert created and all(task.cancelled() for task in created)
    assert not started
    assert not list(Path(directory).iterdir())
    assert preserved.read_text() == "keep"
    assert not runtime._active
    await runtime.aclose()
    assert not runtime._tracker.processes and not runtime._tracker._timers
