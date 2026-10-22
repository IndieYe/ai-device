"""Background task tracking."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from .models import BackgroundProcess
from .output_capture import (
    compute_returned_output_bytes,
    get_total_bytes,
    read_preview_file,
)

COMPLETED_PROCESS_RETENTION_SECONDS = 3600


class BackgroundProcessTracker:
    """One runtime's background results and retention timers (one event loop)."""

    def __init__(self, retention_seconds: float = COMPLETED_PROCESS_RETENTION_SECONDS):
        if retention_seconds < 0:
            raise ValueError("retention_seconds must be non-negative")
        self.retention_seconds = retention_seconds
        self.processes: dict[int, BackgroundProcess] = {}
        self._timers: dict[int, asyncio.TimerHandle] = {}
        self._closed = False

    def register(self, process: BackgroundProcess) -> None:
        self.processes[process.pid] = process
        process.task.add_done_callback(lambda _: self.schedule_cleanup(process))

    def schedule_cleanup(self, process: BackgroundProcess) -> None:
        if self._closed or self.processes.get(process.pid) is not process:
            return
        old_timer = self._timers.pop(process.pid, None)
        if old_timer is not None:
            old_timer.cancel()
        # Observe exceptional drains even if no caller ever polls them.
        if process.task.done() and not process.task.cancelled():
            process.task.exception()

        def cleanup() -> None:
            self._timers.pop(process.pid, None)
            if self.processes.get(process.pid) is process and process.task.done():
                self.processes.pop(process.pid, None)

        self._timers[process.pid] = asyncio.get_running_loop().call_later(
            self.retention_seconds, cleanup
        )

    def find(self, pid: int) -> tuple[BackgroundProcess | None, dict[str, Any]]:
        process = self.processes.get(pid)
        return (
            (process, {}) if process is not None else (None, not_running_response(pid))
        )

    async def aclose(self) -> None:
        self._closed = True
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        tasks = [process.task for process in self.processes.values()]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.processes.clear()

    async def get_background_result(
        self,
        pid: int,
        timeout: float | None = 0,
        output_bytes: int = 200,
    ) -> Dict[str, Any]:
        if output_bytes < 0:
            return error_response("INVALID_BYTES", "bytes 不能小于 0")
        if timeout is not None and timeout < 0:
            return error_response("INVALID_TIMEOUT", "timeout 不能小于 0")
        try:
            return await self._get_background_result(pid, timeout, output_bytes)
        except Exception as exc:  # noqa: BLE001 - convert drain failure into a result
            process = self.processes.get(pid)
            return await asyncio.to_thread(
                error_response,
                "EXECUTION_ERROR",
                f"后台结果读取失败: {exc}",
                process.output_path if process is not None else None,
            )

    async def _get_background_result(
        self,
        pid: int,
        timeout: Optional[float] = 0,
        output_bytes: int = 200,
    ) -> Dict[str, Any]:
        matched_process, lookup_error = self.find(pid)
        if matched_process is None:
            return lookup_error

        process = matched_process
        if process.task.done():
            exit_code = process.task.result()
            if exit_code == 0:
                return await asyncio.to_thread(
                    background_response,
                    process=process,
                    code="OK",
                    message="命令执行成功",
                    exit_code=0,
                    output_bytes=output_bytes,
                )
            return await asyncio.to_thread(
                background_response,
                process=process,
                code="COMMAND_ERROR",
                message=f"命令执行完成，退出码 {exit_code}",
                exit_code=exit_code,
                output_bytes=output_bytes,
            )

        if timeout is not None and timeout > 0:
            try:
                exit_code = await asyncio.wait_for(
                    asyncio.shield(process.task), timeout
                )
            except asyncio.TimeoutError:
                return await asyncio.to_thread(
                    background_response,
                    process=process,
                    code="RUNNING",
                    message=f"目标进程仍在运行: {pid}",
                    exit_code=None,
                    output_bytes=output_bytes,
                )

            if exit_code == 0:
                return await asyncio.to_thread(
                    background_response,
                    process=process,
                    code="OK",
                    message="命令执行成功",
                    exit_code=0,
                    output_bytes=output_bytes,
                )
            return await asyncio.to_thread(
                background_response,
                process=process,
                code="COMMAND_ERROR",
                message=f"命令执行完成，退出码 {exit_code}",
                exit_code=exit_code,
                output_bytes=output_bytes,
            )

        return await asyncio.to_thread(
            background_response,
            process=process,
            code="RUNNING",
            message=f"目标进程仍在运行: {pid}",
            exit_code=None,
            output_bytes=output_bytes,
        )


def not_running_response(pid: int) -> Dict[str, Any]:
    output = ""
    return {
        "success": False,
        "code": "NOT_RUNNING",
        "message": f"目标进程不在运行中: {pid}",
        "exit_code": None,
        "output": output,
        "output_path": None,
        "truncated": False,
        "returned_output_bytes": compute_returned_output_bytes(output),
        "total_bytes": None,
        "duration_ms": 0,
        "background_pid": None,
        "tool_mode": None,
    }


def error_response(
    code: str, message: str, output_path: Optional[str] = None
) -> Dict[str, Any]:
    output = ""
    return {
        "success": False,
        "code": code,
        "message": message,
        "exit_code": None,
        "output": output,
        "output_path": output_path,
        "truncated": False,
        "returned_output_bytes": compute_returned_output_bytes(output),
        "total_bytes": get_total_bytes(output_path),
        "duration_ms": 0,
        "background_pid": None,
        "tool_mode": None,
    }


def background_response(
    process: BackgroundProcess,
    code: str,
    message: str,
    exit_code: Optional[int],
    output_bytes: int,
) -> Dict[str, Any]:
    output_text, truncated, total_bytes = read_preview_file(
        process.output_path, output_bytes, prefer_tail_when_small=True
    )
    duration_ms = int((time.monotonic() - process.start_time) * 1000)
    output = output_text
    return {
        "success": exit_code is None or exit_code == 0,
        "code": code,
        "message": message,
        "exit_code": exit_code,
        "output": output,
        "output_path": process.output_path,
        "truncated": truncated,
        "returned_output_bytes": compute_returned_output_bytes(output),
        "total_bytes": total_bytes,
        "duration_ms": duration_ms,
        "background_pid": process.pid if exit_code is None else None,
        "tool_mode": process.tool_mode,
    }
