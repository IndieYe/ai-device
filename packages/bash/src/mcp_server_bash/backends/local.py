"""Local execution backend."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from typing import Optional

from ..models import ProcessExecutionInfo, ScriptSyntaxError

_cached_shell: Optional[tuple[str, list[str]]] = None


def get_shell_config() -> tuple[str, list[str]]:
    global _cached_shell
    if _cached_shell is not None:
        return _cached_shell

    if os.path.exists("/bin/bash"):
        _cached_shell = ("/bin/bash", ["-c"])
        return _cached_shell

    try:
        result = subprocess.run(
            ["which", "bash"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip().split("\n")[0]
            _cached_shell = (path, ["-c"])
            return _cached_shell
    except Exception:
        pass

    _cached_shell = ("sh", ["-c"])
    return _cached_shell


def get_shell_env() -> dict[str, str]:
    return dict(os.environ)


def merge_shell_env(env_overrides: Optional[dict[str, str]] = None) -> dict[str, str]:
    env = get_shell_env()
    if env_overrides:
        env.update(env_overrides)
    return env


def kill_local_process_group(pid: int) -> None:
    try:
        os.kill(-pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


class LocalExecutionBackend:
    async def run_command(
        self,
        command: str,
        cwd: str,
        env_overrides: Optional[dict[str, str]] = None,
    ) -> ProcessExecutionInfo:
        shell, args = get_shell_config()
        env = merge_shell_env(env_overrides)
        process = await asyncio.create_subprocess_exec(
            shell,
            *args,
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        return ProcessExecutionInfo(
            process=process,
            pid=process.pid,
        )

    async def run_argv(
        self,
        argv: tuple[str, ...],
        cwd: str,
        env_overrides: Optional[dict[str, str]] = None,
        stdin_path: Optional[str] = None,
    ) -> ProcessExecutionInfo:
        env = merge_shell_env(env_overrides)
        stdin_stream = open(stdin_path, "rb") if stdin_path is not None else subprocess.DEVNULL
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=env,
                stdin=stdin_stream,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        finally:
            if stdin_path is not None:
                stdin_stream.close()
        return ProcessExecutionInfo(
            process=process,
            pid=process.pid,
        )

    async def run_script(
        self,
        script_path: str,
        cwd: str,
        env_overrides: Optional[dict[str, str]] = None,
        stdin_path: Optional[str] = None,
    ) -> ProcessExecutionInfo:
        shell, _ = get_shell_config()
        env = merge_shell_env(env_overrides)
        syntax_process = await asyncio.create_subprocess_exec(
            shell,
            "-n",
            script_path,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await syntax_process.communicate()
        except asyncio.CancelledError:
            kill_local_process_group(syntax_process.pid)
            await syntax_process.wait()
            raise
        if syntax_process.returncode != 0:
            output = stdout + stderr
            max_syntax_output_bytes = 65536
            raise ScriptSyntaxError(
                output[:max_syntax_output_bytes],
                truncated=len(output) > max_syntax_output_bytes,
            )

        stdin_stream = open(stdin_path, "rb") if stdin_path is not None else subprocess.DEVNULL
        try:
            process = await asyncio.create_subprocess_exec(
                shell,
                script_path,
                cwd=cwd,
                env=env,
                stdin=stdin_stream,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        finally:
            if stdin_path is not None:
                stdin_stream.close()
        return ProcessExecutionInfo(
            process=process,
            pid=process.pid,
        )
