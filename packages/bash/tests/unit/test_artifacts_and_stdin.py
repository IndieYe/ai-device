import json
import os
from pathlib import Path
import stat
import sys
import time
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

from mcp_server_bash import artifacts as artifact_manager
from mcp_server_bash import server


class _EmptyContext:
    request_context = None


def _tool_result_content(result):
    if isinstance(result.content, dict):
        return result.content
    return json.loads(result.content[0].text)


def test_create_stdin_artifact_is_private_and_cleanup_is_idempotent(tmp_path):
    root = tmp_path / "artifacts"
    stdin_path, execution_artifacts = artifact_manager.create_stdin_artifact(
        "中文\n$HOME\n".encode(),
        str(root),
    )
    path = Path(stdin_path)

    assert path.read_bytes() == "中文\n$HOME\n".encode()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    artifact_manager.cleanup_artifacts(execution_artifacts, str(root))
    artifact_manager.cleanup_artifacts(execution_artifacts, str(root))
    assert not path.parent.exists()


def test_cleanup_artifacts_refuses_paths_outside_managed_root(tmp_path):
    root = tmp_path / "artifacts"
    outside = tmp_path / ("a" * 32)
    outside.mkdir()
    marker = outside / "keep"
    marker.write_text("safe")

    artifact_manager.cleanup_artifacts(
        artifact_manager.ExecutionArtifacts(local_paths=(str(outside),)),
        str(root),
    )

    assert marker.exists()


def test_cleanup_expired_artifacts_only_removes_valid_old_directories(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    old = root / ("a" * 32)
    fresh = root / ("b" * 32)
    invalid = root / "not-an-execution"
    old.mkdir()
    fresh.mkdir()
    invalid.mkdir()
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(fresh, (now, now))
    os.utime(invalid, (now - 100, now - 100))

    removed = artifact_manager.cleanup_expired_artifacts(
        str(root),
        50,
        now=now,
    )

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()
    assert invalid.exists()


@pytest.mark.parametrize("stdin", ["", "中文\nline2\n$HOME"])
@pytest.mark.asyncio
async def test_local_run_reads_stdin_exactly_and_cleans_artifact(runtime, tmp_path, stdin):
    artifact_root = tmp_path / "artifacts"
    stdin_path, execution_artifacts = artifact_manager.create_stdin_artifact(
        stdin.encode(),
        str(artifact_root),
    )
    execution_dir = Path(stdin_path).parent

    result = await runtime.run_argv(
        argv=(sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        stdin_path=stdin_path,
        artifacts=execution_artifacts,
        artifact_dir=str(artifact_root),
    )

    assert result["code"] == "OK"
    assert result["output"] == stdin
    assert not execution_dir.exists()


@pytest.mark.asyncio
async def test_large_stdin_does_not_block_background_return_and_cleans_after_finish(runtime,
    monkeypatch,
    tmp_path,
):
    artifact_root = tmp_path / "artifacts"
    stdin_path, execution_artifacts = artifact_manager.create_stdin_artifact(
        b"x" * (1024 * 1024),
        str(artifact_root),
    )
    execution_dir = Path(stdin_path).parent

    started = time.monotonic()
    result = await runtime.run_argv(
        argv=(sys.executable, "-c", "import time; time.sleep(0.15)"),
        cwd=str(tmp_path),
        timeout=0.01,
        output_dir=str(tmp_path),
        stdin_path=stdin_path,
        artifacts=execution_artifacts,
        artifact_dir=str(artifact_root),
    )

    assert result["code"] == "BACKGROUND"
    assert time.monotonic() - started < 0.5
    assert execution_dir.exists()

    finished = await runtime.get_background_result(
        result["background_pid"],
        timeout=1,
        output_bytes=100,
    )
    assert finished["code"] == "OK"
    assert not execution_dir.exists()


@pytest.mark.asyncio
async def test_run_tool_rejects_large_stdin_before_creating_artifact(monkeypatch):
    monkeypatch.setattr(server, "BASH_MAX_STDIN_BYTES", 3)
    create = pytest.importorskip("unittest.mock").Mock()
    monkeypatch.setattr(server, "create_stdin_artifact", create)

    result = await server.run(
        purpose="验证标准输入大小限制",
        argv=["cat"],
        stdin="中文",
        ctx=_EmptyContext(),
    )

    content = _tool_result_content(result)
    assert content["code"] == "STDIN_TOO_LARGE"
    assert content["tool_mode"] == "run"
    create.assert_not_called()


@pytest.mark.asyncio
async def test_run_tool_summary_and_logs_do_not_include_stdin(monkeypatch, caplog, tmp_path):
    secret = "stdin-super-secret"
    monkeypatch.setattr(server, "BASH_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(server, "BASH_OUTPUT_DIR", str(tmp_path / "outputs"))

    result = await server.run(
        purpose="验证标准输入不进入摘要",
        argv=[sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"],
        stdin=secret,
        cwd=str(tmp_path),
        ctx=_EmptyContext(),
    )

    content = _tool_result_content(result)
    assert content["code"] == "OK"
    assert secret not in result.meta["summary"]
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_start_failure_cleans_stdin_artifact(runtime, tmp_path):
    artifact_root = tmp_path / "artifacts"
    stdin_path, execution_artifacts = artifact_manager.create_stdin_artifact(
        b"input",
        str(artifact_root),
    )
    execution_dir = Path(stdin_path).parent

    result = await runtime.run_argv(
        argv=(str(tmp_path / "missing-program"),),
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        stdin_path=stdin_path,
        artifacts=execution_artifacts,
        artifact_dir=str(artifact_root),
    )

    assert result["code"] == "EXECUTION_ERROR"
    assert not execution_dir.exists()


@pytest.mark.asyncio
async def test_timeout_kill_cleans_stdin_artifact(runtime, tmp_path):
    artifact_root = tmp_path / "artifacts"
    stdin_path, execution_artifacts = artifact_manager.create_stdin_artifact(
        b"input",
        str(artifact_root),
    )
    execution_dir = Path(stdin_path).parent

    result = await runtime.run_argv(
        argv=(sys.executable, "-c", "import time; time.sleep(10)"),
        cwd=str(tmp_path),
        timeout=0.01,
        timeout_behavior="kill",
        output_dir=str(tmp_path),
        stdin_path=stdin_path,
        artifacts=execution_artifacts,
        artifact_dir=str(artifact_root),
    )

    assert result["code"] == "TIMEOUT"
    assert not execution_dir.exists()
