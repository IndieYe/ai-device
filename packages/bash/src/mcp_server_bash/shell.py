"""Shell discovery and command execution orchestration."""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any, Dict, Literal, Optional

from .artifacts import cleanup_artifacts, default_artifact_dir
from .backends.local import LocalExecutionBackend, kill_local_process_group
from .models import (
    BackgroundProcess,
    ExecutionArtifacts,
    ExecutionRequest,
    ExecutionTarget,
    ProcessExecutionInfo,
    ScriptSyntaxError,
)
from .output_capture import (
    HeadBuffer,
    OutputLimitGuard,
    compute_returned_output_bytes,
    create_output_path,
    default_output_dir,
    finish_output_tasks,
    get_total_bytes,
    read_preview_file,
    read_stream,
    sanitize_output_text,
    write_output,
)
from .tracking import BackgroundProcessTracker

ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LOCAL_BACKEND = LocalExecutionBackend()
OUTPUT_FINISH_TIMEOUT_SECONDS = 2.0


def is_valid_env_var_name(name: str) -> bool:
    return bool(ENV_VAR_NAME_RE.fullmatch(name))


class ExecutionRuntime:
    """Reusable local execution owner; no MCP, request context or device identity.

    Use on one event loop, preferably with ``async with``. Closing cancels and
    reaps only this runtime's active executions and background drains. Output
    files remain caller-owned; managed stdin/script files are cleaned by drains.
    """

    def __init__(
        self, *, output_dir: str | None = None, artifact_dir: str | None = None,
        completed_process_retention_seconds: float = 3600,
    ):
        self.output_dir = output_dir or default_output_dir()
        self.artifact_dir = artifact_dir or default_artifact_dir()
        self._tracker = BackgroundProcessTracker(completed_process_retention_seconds)
        self._active: set[asyncio.Task] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._close_task: asyncio.Task | None = None

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("ExecutionRuntime must be used on one event loop")

    def _check_open(self) -> None:
        self._bind_loop()
        if self._closed:
            raise RuntimeError("ExecutionRuntime is closed")

    async def __aenter__(self):
        self._check_open()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.aclose()

    async def aclose(self) -> None:
        """Idempotent, cancellation-safe cleanup of this runtime's resources."""
        self._bind_loop()
        self._closed = True
        if self._close_task is None:
            async def cleanup() -> None:
                active = list(self._active)
                for task in active:
                    task.cancel()
                await asyncio.gather(*active, return_exceptions=True)
                await self._tracker.aclose()

            self._close_task = asyncio.create_task(cleanup())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def get_background_result(
        self, pid: int, timeout: float | None = 0, output_bytes: int = 200,
    ) -> dict[str, Any]:
        self._check_open()
        return await self._tracker.get_background_result(pid, timeout, output_bytes)

    async def run_execution(
        self, request: ExecutionRequest, timeout: float | None = None,
        max_output_bytes: int = 8192, output_bytes: int | None = None,
        timeout_behavior: Literal["background", "kill"] = "background",
        output_dir: str | None = None, max_output_file_bytes: int | None = None,
        artifact_dir: str | None = None,
    ) -> dict[str, Any]:
        self._check_open()
        # Own the execution, not its caller: the caller may re-enter aclose()
        # from __aexit__ or finally while an external close is waiting for us.
        started = False

        async def owned_execution():
            nonlocal started
            started = True
            return await self._run_execution(
                request, timeout, max_output_bytes, output_bytes, timeout_behavior,
                output_dir, max_output_file_bytes, artifact_dir,
            )

        task = asyncio.create_task(owned_execution())
        self._active.add(task)
        try:
            return await task
        finally:
            self._active.discard(task)
            # A task cancelled before its first step never enters the normal
            # execution cleanup; the accepted request still owns these files.
            if not started:
                _cleanup_request_artifacts(request, artifact_dir or self.artifact_dir)

    async def run_command(
        self,
        command: str,
        cwd: str,
        timeout: Optional[float] = None,
        max_output_bytes: int = 8192,
        output_bytes: Optional[int] = None,
        timeout_behavior: Literal["background", "kill"] = "background",
        output_dir: Optional[str] = None,
        max_output_file_bytes: Optional[int] = None,
        env_overrides: Optional[dict[str, str]] = None,
        target: Optional[ExecutionTarget] = None,
    ) -> Dict[str, Any]:
        target = target or ExecutionTarget(
            cwd=cwd,
        )
        request = ExecutionRequest(
            tool_mode="bash",
            command=command,
            argv=None,
            script_path=None,
            stdin_path=None,
            artifacts=None,
            env_overrides=dict(env_overrides or {}),
            target=target,
        )
        return await self.run_execution(
            request=request,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            output_bytes=output_bytes,
            timeout_behavior=timeout_behavior,
            output_dir=output_dir,
            max_output_file_bytes=max_output_file_bytes,
        )


    async def run_argv(
        self,
        argv: tuple[str, ...],
        cwd: str,
        timeout: Optional[float] = None,
        max_output_bytes: int = 8192,
        output_bytes: Optional[int] = None,
        timeout_behavior: Literal["background", "kill"] = "background",
        output_dir: Optional[str] = None,
        max_output_file_bytes: Optional[int] = None,
        env_overrides: Optional[dict[str, str]] = None,
        target: Optional[ExecutionTarget] = None,
        stdin_path: Optional[str] = None,
        artifacts: Optional[ExecutionArtifacts] = None,
        artifact_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = target or ExecutionTarget(
            cwd=cwd,
        )
        request = ExecutionRequest(
            tool_mode="run",
            command=None,
            argv=argv,
            script_path=None,
            stdin_path=stdin_path,
            artifacts=artifacts,
            env_overrides=dict(env_overrides or {}),
            target=target,
        )
        return await self.run_execution(
            request=request,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            output_bytes=output_bytes,
            timeout_behavior=timeout_behavior,
            output_dir=output_dir,
            max_output_file_bytes=max_output_file_bytes,
            artifact_dir=artifact_dir,
        )


    async def run_script(
        self,
        script_path: str,
        cwd: str,
        timeout: Optional[float] = None,
        max_output_bytes: int = 8192,
        output_bytes: Optional[int] = None,
        timeout_behavior: Literal["background", "kill"] = "background",
        output_dir: Optional[str] = None,
        max_output_file_bytes: Optional[int] = None,
        env_overrides: Optional[dict[str, str]] = None,
        target: Optional[ExecutionTarget] = None,
        stdin_path: Optional[str] = None,
        artifacts: Optional[ExecutionArtifacts] = None,
        artifact_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = target or ExecutionTarget(
            cwd=cwd,
        )
        request = ExecutionRequest(
            tool_mode="script",
            command=None,
            argv=None,
            script_path=script_path,
            stdin_path=stdin_path,
            artifacts=artifacts,
            env_overrides=dict(env_overrides or {}),
            target=target,
        )
        return await self.run_execution(
            request=request,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            output_bytes=output_bytes,
            timeout_behavior=timeout_behavior,
            output_dir=output_dir,
            max_output_file_bytes=max_output_file_bytes,
            artifact_dir=artifact_dir,
        )


    async def _run_execution(
        self,
        request: ExecutionRequest,
        timeout: Optional[float] = None,
        max_output_bytes: int = 8192,
        output_bytes: Optional[int] = None,
        timeout_behavior: Literal["background", "kill"] = "background",
        output_dir: Optional[str] = None,
        max_output_file_bytes: Optional[int] = None,
        artifact_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        output_path: Optional[str] = None
        managed_artifact_dir = artifact_dir or self.artifact_dir
        try:
            output_path = create_output_path(output_dir or self.output_dir)
        except Exception:
            output_path = None

        if timeout_behavior not in {"background", "kill"}:
            _cleanup_request_artifacts(request, managed_artifact_dir)
            return _simple_error(
                code="INVALID_TIMEOUT_BEHAVIOR",
                message="timeout_behavior 必须是 background 或 kill",
                exit_code=-1,
                output_path=output_path,
                tool_mode=request.tool_mode,
            )

        if output_bytes is not None and output_bytes < 0:
            _cleanup_request_artifacts(request, managed_artifact_dir)
            return _simple_error(
                code="INVALID_BYTES",
                message="bytes 不能小于 0",
                exit_code=-1,
                output_path=output_path,
                tool_mode=request.tool_mode,
            )

        if output_path is None:
            _cleanup_request_artifacts(request, managed_artifact_dir)
            return _simple_error(
                code="EXECUTION_ERROR",
                message="无法创建命令输出文件",
                exit_code=-1,
                output_path=None,
                tool_mode=request.tool_mode,
            )

        target = request.target
        inline_output_bytes = max_output_bytes if output_bytes is None else min(output_bytes, max_output_bytes)
        start_time = time.monotonic()

        try:
            execution = await _start_execution(request)
        except asyncio.CancelledError:
            _cleanup_request_artifacts(request, managed_artifact_dir)
            raise
        except FileNotFoundError as exc:
            _cleanup_request_artifacts(request, managed_artifact_dir)
            if not os.path.isdir(target.cwd):
                code = "CWD_NOT_FOUND"
                label = "工作目录不存在"
            else:
                return _simple_error(
                    code="EXECUTION_ERROR",
                    message=f"无法启动命令: {exc}",
                    exit_code=-1,
                    output_path=output_path,
                    tool_mode=request.tool_mode,
                )
            return _simple_error(
                code=code,
                message=f"{label}: {exc}",
                exit_code=-1,
                output_path=output_path,
                tool_mode=request.tool_mode,
            )
        except ScriptSyntaxError as exc:
            _cleanup_request_artifacts(request, managed_artifact_dir)
            syntax_output = exc.output
            try:
                with open(output_path, "wb") as stream:
                    stream.write(syntax_output)
            except OSError:
                pass
            returned_bytes = syntax_output[:inline_output_bytes]
            output_text = sanitize_output_text(returned_bytes.decode("utf-8", errors="replace"))
            return {
                "success": False,
                "code": "SCRIPT_SYNTAX_ERROR",
                "message": "脚本语法检查失败，未执行脚本",
                "exit_code": -1,
                "output": output_text,
                "output_path": output_path,
                "truncated": exc.truncated or len(syntax_output) > inline_output_bytes,
                "returned_output_bytes": compute_returned_output_bytes(output_text),
                "total_bytes": get_total_bytes(output_path),
                "duration_ms": int((time.monotonic() - start_time) * 1000),
                "background_pid": None,
                "tool_mode": request.tool_mode,
            }
        except ValueError as exc:
            _cleanup_request_artifacts(request, managed_artifact_dir)
            code = "EXECUTION_ERROR"
            return _simple_error(
                code=code,
                message=str(exc),
                exit_code=-1,
                output_path=output_path,
                tool_mode=request.tool_mode,
            )
        except Exception as exc:
            _cleanup_request_artifacts(request, managed_artifact_dir)
            code = "EXECUTION_ERROR"
            message = f"无法启动命令: {exc}"
            return _simple_error(
                code=code,
                message=message,
                exit_code=-1,
                output_path=output_path,
                duration_ms=int((time.monotonic() - start_time) * 1000),
                tool_mode=request.tool_mode,
            )

        proc = execution.process
        head = HeadBuffer(inline_output_bytes, enabled=True)
        # Bound read-ahead while the output worker is slow (64 x 8192-byte chunks).
        output_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=64)
        guard = OutputLimitGuard(max_output_file_bytes)
        writer = asyncio.create_task(write_output(output_queue, output_path, head, guard))
        readers = (
            asyncio.create_task(read_stream(proc.stdout, output_queue)),
            asyncio.create_task(read_stream(proc.stderr, output_queue)),
        )
        output_limit_task = asyncio.create_task(_stop_on_output_limit(guard, request, execution, writer))

        async def background_drain() -> int:
            exit_code: Optional[int] = None
            try:
                exit_code = await proc.wait()
                await finish_output_tasks(readers, output_queue, writer)
                # A short-lived child may exit zero before the budget watcher
                # terminates it. Drained output, not that race, decides success.
                return -1 if guard.exceeded.is_set() else exit_code
            except asyncio.CancelledError:
                if exit_code is None:
                    try:
                        await _terminate_execution(request, execution)
                    except BaseException:
                        kill_local_process_group(execution.pid)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except BaseException:
                        kill_local_process_group(execution.pid)
                await _finish_output_tasks_bounded(readers, output_queue, writer)
                return exit_code if exit_code is not None and not guard.exceeded.is_set() else -1
            finally:
                _cleanup_request_artifacts(request, managed_artifact_dir)
                output_limit_task.cancel()
                await asyncio.gather(output_limit_task, return_exceptions=True)

        background_task: Optional[asyncio.Task] = None
        try:
            try:
                exit_code = (
                    await asyncio.wait_for(proc.wait(), timeout=timeout)
                    if timeout is not None and timeout > 0
                    else await proc.wait()
                )
            except asyncio.TimeoutError:
                if timeout_behavior == "kill":
                    try:
                        await _terminate_execution(request, execution)
                    except Exception as exc:
                        kill_local_process_group(execution.pid)
                        await _finish_output_tasks_bounded(readers, output_queue, writer)
                        _cleanup_request_artifacts(request, managed_artifact_dir)
                        output_text, truncated, _ = read_preview_file(output_path, inline_output_bytes)
                        return _timeout_or_kill_error(
                            code="TIMEOUT",
                            message=f"显式终止失败: {exc}",
                            output_text=output_text,
                            output_path=output_path,
                            truncated=truncated,
                            duration_ms=int((time.monotonic() - start_time) * 1000),
                            tool_mode=request.tool_mode,
                        )

                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except Exception:
                        pass
                    await finish_output_tasks(readers, output_queue, writer)
                    _cleanup_request_artifacts(request, managed_artifact_dir)
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    output_text, truncated, _ = read_preview_file(output_path, inline_output_bytes)
                    return _timeout_or_kill_error(
                        code="TIMEOUT",
                        message=f"命令执行超时（{timeout}秒），进程已终止",
                        output_text=output_text,
                        output_path=output_path,
                        truncated=truncated,
                        duration_ms=duration_ms,
                        tool_mode=request.tool_mode,
                    )

                try:
                    await asyncio.wait_for(output_queue.join(), timeout=0.2)
                except asyncio.TimeoutError:
                    pass
                if guard.exceeded.is_set():
                    _cleanup_request_artifacts(request, managed_artifact_dir)
                    return _simple_error(
                        code="OUTPUT_LIMIT_EXCEEDED",
                        message=f"命令输出超过 {guard.limit_bytes} 字节上限，输出文件已清空",
                        exit_code=-1,
                        output_path=output_path,
                        tool_mode=request.tool_mode,
                        duration_ms=int((time.monotonic() - start_time) * 1000),
                    )
                duration_ms = int((time.monotonic() - start_time) * 1000)
                output_text, was_truncated, total_bytes = await asyncio.to_thread(
                    read_preview_file, output_path, inline_output_bytes
                )
                head.stop_capture()
                background_task = asyncio.create_task(background_drain())
                await asyncio.sleep(0)
                background = BackgroundProcess(
                    pid=execution.pid,
                    display_command=_display_command(request),
                    output_path=output_path,
                    start_time=start_time,
                    task=background_task,
                    tool_mode=request.tool_mode,
                    artifacts=request.artifacts,
                )
                self._tracker.register(background)
                return {
                    "success": True,
                    "code": "BACKGROUND",
                    "message": f"命令执行超时（{timeout}秒），已转入后台继续运行",
                    "exit_code": None,
                    "output": output_text,
                    "output_path": output_path,
                    "truncated": was_truncated,
                    "returned_output_bytes": compute_returned_output_bytes(output_text),
                    "total_bytes": total_bytes,
                    "duration_ms": duration_ms,
                    "background_pid": execution.pid,
                    "tool_mode": request.tool_mode,
                }

            await finish_output_tasks(readers, output_queue, writer)
            _cleanup_request_artifacts(request, managed_artifact_dir)
            if guard.exceeded.is_set():
                return _simple_error(
                    code="OUTPUT_LIMIT_EXCEEDED",
                    message=f"命令输出超过 {guard.limit_bytes} 字节上限，输出文件已清空",
                    exit_code=-1,
                    output_path=output_path,
                    tool_mode=request.tool_mode,
                    duration_ms=int((time.monotonic() - start_time) * 1000),
                )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            output_text, truncated, total_bytes = read_preview_file(output_path, inline_output_bytes)

            return {
                "success": exit_code == 0,
                "code": "OK" if exit_code == 0 else "COMMAND_ERROR",
                "message": "命令执行成功" if exit_code == 0 else f"命令执行完成，退出码 {exit_code}",
                "exit_code": exit_code,
                "output": output_text,
                "output_path": output_path,
                "truncated": truncated,
                "returned_output_bytes": compute_returned_output_bytes(output_text),
                "total_bytes": total_bytes,
                "duration_ms": duration_ms,
                "background_pid": None,
                "tool_mode": request.tool_mode,
            }
        except BaseException:
            # Failed output capture must release the same resources as cancellation.
            async def cancel_and_reap() -> None:
                try:
                    # A drain task may exist before ownership is published in the index.
                    if background_task is not None:
                        background_task.cancel()
                        await asyncio.gather(background_task, return_exceptions=True)
                    if proc.returncode is None:
                        try:
                            await _terminate_execution(request, execution)
                        except Exception:
                            kill_local_process_group(execution.pid)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        kill_local_process_group(execution.pid)
                    if not all(task.done() for task in (*readers, writer)):
                        await _finish_output_tasks_bounded(readers, output_queue, writer)
                finally:
                    _cleanup_request_artifacts(request, managed_artifact_dir)
                    output_limit_task.cancel()
                    await asyncio.gather(output_limit_task, return_exceptions=True)

            # Finish bounded teardown before returning cancellation, even if cancelled again.
            cleanup_task = asyncio.create_task(cancel_and_reap())
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    continue
            cleanup_task.result()
            raise


async def _start_execution(request: ExecutionRequest) -> ProcessExecutionInfo:
    target = request.target
    if not os.path.isdir(target.cwd):
        raise FileNotFoundError(target.cwd)
    if request.tool_mode == "bash" and request.command is not None:
        return await LOCAL_BACKEND.run_command(
            command=request.command,
            cwd=target.cwd,
            env_overrides=request.env_overrides,
        )
    if request.tool_mode == "run" and request.argv is not None:
        return await LOCAL_BACKEND.run_argv(
            argv=request.argv,
            cwd=target.cwd,
            env_overrides=request.env_overrides,
            stdin_path=request.stdin_path,
        )
    if request.tool_mode == "script" and request.script_path is not None:
        return await LOCAL_BACKEND.run_script(
            script_path=request.script_path,
            cwd=target.cwd,
            env_overrides=request.env_overrides,
            stdin_path=request.stdin_path,
        )
    raise ValueError(f"unsupported local tool mode: {request.tool_mode}")


async def _terminate_execution(
    request: ExecutionRequest,
    execution: ProcessExecutionInfo,
) -> None:
    kill_local_process_group(execution.pid)


async def _stop_on_output_limit(
    guard: OutputLimitGuard,
    request: ExecutionRequest,
    execution: ProcessExecutionInfo,
    writer: asyncio.Task,
) -> None:
    """Terminate the command as soon as its on-disk output crosses the cap.

    Races the cap against the writer task, so the watcher retires on its own once
    the execution has finished normally and never needs explicit cancellation.
    """
    exceeded = asyncio.ensure_future(guard.exceeded.wait())
    failed = asyncio.ensure_future(guard.failed.wait())
    try:
        done, _ = await asyncio.wait(
            {exceeded, failed, writer},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for waiter in (exceeded, failed):
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(exceeded, failed, return_exceptions=True)

    if exceeded not in done and failed not in done and (writer.cancelled() or writer.exception() is None):
        return
    # A disk writer failure must also stop its owned producer; otherwise a
    # bounded queue leaves the child blocked on its pipe indefinitely.
    try:
        await _terminate_execution(request, execution)
    except Exception:
        kill_local_process_group(execution.pid)


async def _finish_output_tasks_bounded(
    readers: tuple[asyncio.Task, asyncio.Task],
    queue: asyncio.Queue[Optional[bytes]],
    writer: asyncio.Task,
) -> None:
    try:
        await asyncio.wait_for(
            finish_output_tasks(readers, queue, writer),
            timeout=OUTPUT_FINISH_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        for task in (*readers, writer):
            if not task.done():
                task.cancel()
        await asyncio.gather(*readers, writer, return_exceptions=True)


def _cleanup_request_artifacts(request: ExecutionRequest, artifact_dir: str) -> None:
    cleanup_artifacts(request.artifacts, artifact_dir)


def _display_command(request: ExecutionRequest) -> str:
    if request.tool_mode == "bash":
        return request.command or ""
    if request.tool_mode == "run" and request.argv:
        return request.argv[0]
    return "bash script"


def _simple_error(
    code: str,
    message: str,
    exit_code: int,
    output_path: Optional[str],
    tool_mode: Literal["bash", "run", "script"],
    duration_ms: int = 0,
) -> Dict[str, Any]:
    output = ""
    return {
        "success": False,
        "code": code,
        "message": message,
        "exit_code": exit_code,
        "output": output,
        "output_path": output_path,
        "truncated": False,
        "returned_output_bytes": compute_returned_output_bytes(output),
        "total_bytes": get_total_bytes(output_path),
        "duration_ms": duration_ms,
        "background_pid": None,
        "tool_mode": tool_mode,
    }


def _timeout_or_kill_error(
    code: str,
    message: str,
    output_text: str,
    output_path: str,
    truncated: bool,
    duration_ms: int,
    tool_mode: Literal["bash", "run", "script"],
) -> Dict[str, Any]:
    return {
        "success": False,
        "code": code,
        "message": message,
        "exit_code": -1,
        "output": output_text,
        "output_path": output_path,
        "truncated": truncated,
        "returned_output_bytes": compute_returned_output_bytes(output_text),
        "total_bytes": get_total_bytes(output_path),
        "duration_ms": duration_ms,
        "background_pid": None,
        "tool_mode": tool_mode,
    }
