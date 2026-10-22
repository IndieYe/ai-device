"""Shared execution models and error types."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal, Optional, Protocol


class ProcessHandle(Protocol):
    pid: int
    stdout: asyncio.StreamReader | None
    stderr: asyncio.StreamReader | None

    async def wait(self) -> int: ...

    def kill(self) -> None: ...


@dataclass(frozen=True)
class ExecutionTarget:
    cwd: str


@dataclass(frozen=True)
class ExecutionArtifacts:
    local_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionRequest:
    tool_mode: Literal["bash", "run", "script"]
    command: Optional[str]
    argv: Optional[tuple[str, ...]]
    script_path: Optional[str]
    stdin_path: Optional[str]
    env_overrides: dict[str, str]
    target: ExecutionTarget
    artifacts: Optional[ExecutionArtifacts] = None

    def __post_init__(self) -> None:
        if self.tool_mode not in {"bash", "run", "script"}:
            raise ValueError(f"unsupported tool_mode={self.tool_mode}")
        if self.tool_mode == "bash":
            valid = self.command is not None and self.argv is None and self.script_path is None
        elif self.tool_mode == "run":
            valid = self.command is None and self.argv is not None and self.script_path is None
        elif self.tool_mode == "script":
            valid = self.command is None and self.argv is None and self.script_path is not None
        if not valid:
            raise ValueError(f"invalid execution request for tool_mode={self.tool_mode}")
        if self.tool_mode == "bash" and self.stdin_path is not None:
            raise ValueError("bash execution does not support stdin_path")


@dataclass
class ProcessExecutionInfo:
    process: ProcessHandle
    pid: int


@dataclass
class BackgroundProcess:
    pid: int
    display_command: str
    output_path: str
    start_time: float
    task: asyncio.Task[int]
    tool_mode: Literal["bash", "run", "script"]
    artifacts: Optional[ExecutionArtifacts] = None


class ScriptSyntaxError(RuntimeError):
    def __init__(self, output: bytes, truncated: bool = False):
        super().__init__("script syntax check failed")
        self.output = output
        self.truncated = truncated
