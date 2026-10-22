import asyncio
import os
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

fastmcp_module = ModuleType("fastmcp")
fastmcp_tools_module = ModuleType("fastmcp.tools")
fastmcp_tool_module = ModuleType("fastmcp.tools.tool")
fastmcp_dependencies_module = ModuleType("fastmcp.dependencies")
fastmcp_server_module = ModuleType("fastmcp.server")
fastmcp_server_context_module = ModuleType("fastmcp.server.context")


class _FakeFastMCP:
    def __init__(self, name, **kwargs):
        self.name = name

    def tool(self, **kwargs):
        return lambda func: func

    def run(self, *args, **kwargs):
        return None


class _FakeToolResult:
    def __init__(self, content=None, meta=None):
        self.content = content
        self.meta = meta


class _FakeContext:
    request_context = None


fastmcp_module.FastMCP = _FakeFastMCP
fastmcp_tool_module.ToolResult = _FakeToolResult
fastmcp_dependencies_module.CurrentContext = lambda: _FakeContext()
fastmcp_server_context_module.Context = _FakeContext

sys.modules.setdefault("fastmcp", fastmcp_module)
sys.modules.setdefault("fastmcp.tools", fastmcp_tools_module)
sys.modules.setdefault("fastmcp.tools.tool", fastmcp_tool_module)
sys.modules.setdefault("fastmcp.dependencies", fastmcp_dependencies_module)
sys.modules.setdefault("fastmcp.server", fastmcp_server_module)
sys.modules.setdefault("fastmcp.server.context", fastmcp_server_context_module)

from mcp_server_bash.config import load_app_config
from mcp_server_bash.output_capture import (
    HeadBuffer,
    OutputLimitGuard,
    cleanup_expired_outputs,
    write_output,
)


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _write_output_file(directory: Path, name: str, age_seconds: float = 0.0) -> Path:
    path = directory / name
    path.write_text("payload", encoding="utf-8")
    if age_seconds:
        _age(path, age_seconds)
    return path


# ---------------------------------------------------------------------------
# OutputLimitGuard
# ---------------------------------------------------------------------------


def test_guard_accepts_bytes_up_to_the_cap():
    guard = OutputLimitGuard(10)

    assert guard.accept(4) is True
    assert guard.accept(6) is True
    assert guard.written == 10
    assert guard.exceeded.is_set() is False


def test_guard_rejects_the_chunk_that_would_cross_the_cap():
    guard = OutputLimitGuard(10)

    assert guard.accept(6) is True
    assert guard.accept(5) is False
    assert guard.written == 6
    assert guard.exceeded.is_set() is False


def test_guard_without_limit_accepts_everything():
    guard = OutputLimitGuard(None)

    assert guard.accept(10**9) is True
    assert guard.accept(10**9) is True
    assert guard.exceeded.is_set() is False


# ---------------------------------------------------------------------------
# write_output cap enforcement
# ---------------------------------------------------------------------------


def _run_write_output(tmp_path: Path, chunks: list[bytes], limit: int | None):
    path = tmp_path / "capture.log"
    guard = OutputLimitGuard(limit)

    async def scenario():
        queue: asyncio.Queue = asyncio.Queue()
        for chunk in chunks:
            await queue.put(chunk)
        await queue.put(None)
        await write_output(queue, str(path), HeadBuffer(64), guard)

    asyncio.run(scenario())
    return path, guard


def test_write_output_persists_output_within_the_cap(tmp_path):
    path, guard = _run_write_output(tmp_path, [b"hello ", b"world"], limit=64)

    assert path.read_bytes() == b"hello world"
    assert guard.exceeded.is_set() is False


def test_write_output_clears_file_and_flags_overflow(tmp_path):
    path, guard = _run_write_output(tmp_path, [b"x" * 32, b"y" * 64], limit=48)

    assert guard.exceeded.is_set() is True
    assert path.read_bytes() == b""
    assert path.stat().st_size == 0


@pytest.mark.asyncio
async def test_write_output_keeps_draining_after_overflow(tmp_path):
    """The queue must keep draining so the command never blocks on a full pipe."""
    path = tmp_path / "capture.log"
    guard = OutputLimitGuard(16)

    async def scenario():
        queue: asyncio.Queue = asyncio.Queue()
        for _ in range(5):
            await queue.put(b"z" * 32)
        await queue.put(None)
        await write_output(queue, str(path), HeadBuffer(64), guard)
        assert queue.empty() is True

    await (scenario())
    assert guard.exceeded.is_set() is True


# ---------------------------------------------------------------------------
# run_command integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_command_reports_output_limit_exceeded_and_clears_file(runtime):
    result = await (
        runtime.run_command(
            command="python3 -c 'print(\"x\" * 8000)'",
            cwd=".",
            max_output_bytes=64,
            max_output_file_bytes=1024,
        )
    )

    assert result["success"] is False
    assert result["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert result["exit_code"] == -1
    assert result["output"] == ""
    assert result["total_bytes"] == 0
    assert Path(result["output_path"]).read_bytes() == b""


@pytest.mark.asyncio
async def test_run_command_keeps_complete_output_under_the_limit(runtime):
    result = await (
        runtime.run_command(
            command="python3 -c 'print(\"x\" * 100)'",
            cwd=".",
            max_output_bytes=4096,
            max_output_file_bytes=1048576,
        )
    )

    assert result["success"] is True
    assert result["code"] == "OK"
    assert result["total_bytes"] == 101
    assert Path(result["output_path"]).stat().st_size == 101


@pytest.mark.asyncio
async def test_run_command_without_limit_keeps_large_output(runtime):
    result = await (
        runtime.run_command(
            command="python3 -c 'print(\"x\" * 20000)'",
            cwd=".",
            max_output_bytes=64,
        )
    )

    assert result["success"] is True
    assert result["code"] == "OK"
    assert result["total_bytes"] == 20001


# ---------------------------------------------------------------------------
# cleanup_expired_outputs
# ---------------------------------------------------------------------------


def test_cleanup_removes_outputs_older_than_the_ttl(tmp_path):
    stale = _write_output_file(tmp_path, "20260901-120000-abcdef.log", age_seconds=8 * 86400)
    fresh = _write_output_file(tmp_path, "20260929-120000-abcdef.log", age_seconds=3600)

    removed = cleanup_expired_outputs(str(tmp_path), 7 * 86400)

    assert removed == 1
    assert stale.exists() is False
    assert fresh.exists() is True


def test_cleanup_ignores_foreign_names_symlinks_and_directories(tmp_path):
    foreign = _write_output_file(tmp_path, "notes.txt", age_seconds=30 * 86400)
    partial = _write_output_file(tmp_path, "20260901-120000-abcde.log", age_seconds=30 * 86400)
    subdir = tmp_path / "20260901-120000-abcdef.log"
    subdir.mkdir()
    _age(subdir, 30 * 86400)

    removed = cleanup_expired_outputs(str(tmp_path), 7 * 86400)

    assert removed == 0
    assert foreign.exists() is True
    assert partial.exists() is True
    assert subdir.is_dir() is True


def test_cleanup_skips_symlinks_instead_of_following_them(tmp_path):
    target = _write_output_file(tmp_path, "real-payload.txt", age_seconds=30 * 86400)
    link = tmp_path / "20260901-120000-abcdef.log"
    link.symlink_to(target)

    removed = cleanup_expired_outputs(str(tmp_path), 7 * 86400)

    assert removed == 0
    assert link.is_symlink() is True
    assert target.exists() is True


def test_cleanup_on_missing_directory_returns_zero(tmp_path):
    assert cleanup_expired_outputs(str(tmp_path / "nope"), 7 * 86400) == 0


def test_cleanup_honours_an_explicit_reference_time(tmp_path):
    path = _write_output_file(tmp_path, "20260901-120000-abcdef.log")
    stamp = time.mktime(time.strptime("2026-09-01 12:00:00", "%Y-%m-%d %H:%M:%S"))
    os.utime(path, (stamp, stamp))

    assert cleanup_expired_outputs(str(tmp_path), 7 * 86400, now=stamp + 6 * 86400) == 0
    assert cleanup_expired_outputs(str(tmp_path), 7 * 86400, now=stamp + 8 * 86400) == 1


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


@pytest.fixture
def project_with_env(tmp_path):
    def _make(**values):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "\n".join(f"{key}={value}" for key, value in values.items()),
            encoding="utf-8",
        )
        return load_app_config(tmp_path)

    return _make


def test_output_retention_defaults(project_with_env):
    config = project_with_env()

    assert config.bash_output_ttl_seconds == 604800
    assert config.bash_max_output_file_bytes == 104857600


def test_output_retention_reads_env_overrides(project_with_env):
    config = project_with_env(
        BASH_OUTPUT_TTL_SECONDS=3600,
        BASH_MAX_OUTPUT_FILE_BYTES=2048,
    )

    assert config.bash_output_ttl_seconds == 3600
    assert config.bash_max_output_file_bytes == 2048


def test_output_retention_rejects_non_positive_values(project_with_env):
    config = project_with_env(
        BASH_OUTPUT_TTL_SECONDS=0,
        BASH_MAX_OUTPUT_FILE_BYTES=-1,
    )

    assert config.bash_output_ttl_seconds == 1
    assert config.bash_max_output_file_bytes == 1
