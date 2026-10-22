import asyncio
from pathlib import Path
import stat
import sys
from types import ModuleType

import pytest

if "fastmcp" not in sys.modules:
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
    sys.modules["fastmcp"] = fastmcp_module
    sys.modules["fastmcp.tools"] = fastmcp_tools_module
    sys.modules["fastmcp.tools.tool"] = fastmcp_tool_module
    sys.modules["fastmcp.dependencies"] = fastmcp_dependencies_module
    sys.modules["fastmcp.server"] = fastmcp_server_module
    sys.modules["fastmcp.server.context"] = fastmcp_server_context_module

from mcp_server_bash import artifacts as artifact_manager
from mcp_server_bash import server
from mcp_server_bash.models import ExecutionRequest, ExecutionTarget


class _EmptyContext:
    request_context = None


def _configure_temp_dirs(monkeypatch, tmp_path):
    artifact_dir = tmp_path / "artifacts"
    output_dir = tmp_path / "outputs"
    monkeypatch.setattr(server, "BASH_ARTIFACT_DIR", str(artifact_dir))
    monkeypatch.setattr(server, "BASH_OUTPUT_DIR", str(output_dir))
    return artifact_dir, output_dir


def test_execution_request_accepts_script_payload():
    target = ExecutionTarget(cwd="/tmp")
    request = ExecutionRequest(
        tool_mode="script",
        command=None,
        argv=None,
        script_path="/tmp/script.sh",
        stdin_path=None,
        env_overrides={},
        target=target,
    )
    assert request.tool_mode == "script"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("", "INVALID_SCRIPT"),
        (None, "INVALID_SCRIPT"),
        ("echo ok\0echo bad", "INVALID_SCRIPT"),
    ],
)
@pytest.mark.asyncio
async def test_script_rejects_invalid_content_without_creating_artifacts(monkeypatch, value, code):
    create = pytest.importorskip("unittest.mock").Mock()
    monkeypatch.setattr(server, "create_script_artifacts", create)

    result = await (
        server.script(purpose="验证非法脚本内容", script=value, ctx=_EmptyContext())
    )

    assert result.content["code"] == code
    assert result.content["tool_mode"] == "script"
    create.assert_not_called()


@pytest.mark.asyncio
async def test_script_rejects_size_limit(monkeypatch):
    monkeypatch.setattr(server, "BASH_MAX_SCRIPT_BYTES", 3)

    result = await (
        server.script(purpose="验证脚本大小限制", script="中文", ctx=_EmptyContext())
    )

    assert result.content["code"] == "SCRIPT_TOO_LARGE"
    assert result.content["tool_mode"] == "script"


def test_create_script_artifacts_are_private_and_share_execution_dir(tmp_path):
    script_path, stdin_path, execution_artifacts = artifact_manager.create_script_artifacts(
        b"printf ok\n",
        b"input",
        str(tmp_path),
    )

    script_file = Path(script_path)
    stdin_file = Path(stdin_path)
    assert script_file.parent == stdin_file.parent
    assert stat.S_IMODE(script_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(script_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(stdin_file.stat().st_mode) == 0o600
    assert script_file.read_bytes() == b"printf ok\n"
    assert stdin_file.read_bytes() == b"input"

    artifact_manager.cleanup_artifacts(execution_artifacts, str(tmp_path))
    assert not script_file.parent.exists()


@pytest.mark.asyncio
async def test_script_executes_with_stdin_cwd_and_env_then_cleans(monkeypatch, tmp_path):
    artifact_dir, _ = _configure_temp_dirs(monkeypatch, tmp_path)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    secret_script = "script-super-secret"
    secret_stdin = "stdin-super-secret"
    script_text = (
        f"# {secret_script}\n"
        "printf '%s|' \"$SCRIPT_TEST_ENV\"\n"
        "pwd\n"
        "cat\n"
    )

    result = await server.script(
        purpose="验证脚本执行与输入清理",
        script=script_text,
        stdin=secret_stdin,
        cwd=str(work_dir),
        ctx=_EmptyContext(),
    )

    assert result.content["code"] == "OK"
    assert result.content["tool_mode"] == "script"
    assert str(work_dir) in result.content["output"]
    assert secret_stdin in result.content["output"]
    assert result.meta["summary"] == "验证脚本执行与输入清理"
    assert secret_script not in result.meta["summary"]
    assert secret_stdin not in result.meta["summary"]
    assert not artifact_dir.exists() or not list(artifact_dir.iterdir())


@pytest.mark.asyncio
async def test_script_receives_env_overrides(monkeypatch, tmp_path):
    _configure_temp_dirs(monkeypatch, tmp_path)

    class _RequestContext:
        meta = {"shell_env": {"SCRIPT_TEST_ENV": "visible"}}

    class _Context:
        request_context = _RequestContext()

    result = await server.script(
        purpose="验证脚本环境变量覆盖",
        script="printf '%s' \"$SCRIPT_TEST_ENV\"\n",
        ctx=_Context(),
    )

    assert result.content["code"] == "OK"
    assert result.content["output"] == "visible"


@pytest.mark.asyncio
async def test_script_syntax_error_has_zero_side_effect_and_cleans(monkeypatch, tmp_path):
    artifact_dir, _ = _configure_temp_dirs(monkeypatch, tmp_path)
    side_effect = tmp_path / "must-not-exist"

    result = await server.script(
        purpose="验证脚本语法失败无副作用",
        script=f"touch {side_effect!s}\nif then\n",
        ctx=_EmptyContext(),
    )

    assert result.content["code"] == "SCRIPT_SYNTAX_ERROR"
    assert result.content["tool_mode"] == "script"
    assert not side_effect.exists()
    assert not artifact_dir.exists() or not list(artifact_dir.iterdir())


@pytest.mark.asyncio
async def test_script_nonzero_exit_is_command_error_and_cleans(monkeypatch, tmp_path):
    artifact_dir, _ = _configure_temp_dirs(monkeypatch, tmp_path)

    result = await server.script(
        purpose="验证脚本非零退出",
        script="echo before\nexit 7\n",
        ctx=_EmptyContext(),
    )

    assert result.content["success"] is False
    assert result.content["code"] == "COMMAND_ERROR"
    assert result.content["exit_code"] == 7
    assert result.content["tool_mode"] == "script"
    assert not artifact_dir.exists() or not list(artifact_dir.iterdir())


@pytest.mark.asyncio
async def test_script_timeout_kill_cleans_artifacts(monkeypatch, tmp_path):
    artifact_dir, _ = _configure_temp_dirs(monkeypatch, tmp_path)

    result = await server.script(
        purpose="验证脚本超时终止清理",
        script="sleep 5\n",
        timeout=0.05,
        timeout_behavior="kill",
        ctx=_EmptyContext(),
    )

    assert result.content["code"] == "TIMEOUT"
    assert result.content["tool_mode"] == "script"
    assert not artifact_dir.exists() or not list(artifact_dir.iterdir())


@pytest.mark.asyncio
async def test_script_background_get_retains_artifacts_until_finish(runtime, monkeypatch, tmp_path):
    artifact_dir, _ = _configure_temp_dirs(monkeypatch, tmp_path)

    result = await server.script(
        purpose="验证后台脚本资源保留",
        script="sleep 0.2\nprintf done\n",
        timeout=0.02,
        ctx=_EmptyContext(),
    )

    assert result.content["code"] == "BACKGROUND"
    assert result.content["tool_mode"] == "script"
    assert list(artifact_dir.iterdir())

    finished = await runtime.get_background_result(
        result.content["background_pid"],
        timeout=2,
        output_bytes=200,
    )
    assert finished["code"] == "OK"
    assert finished["tool_mode"] == "script"
    for _ in range(50):
        if not artifact_dir.exists() or not list(artifact_dir.iterdir()):
            break
        await asyncio.sleep(0.01)
    assert not artifact_dir.exists() or not list(artifact_dir.iterdir())


@pytest.mark.asyncio
async def test_script_cancellation_terminates_process_and_cleans(monkeypatch, tmp_path):
    artifact_dir, _ = _configure_temp_dirs(monkeypatch, tmp_path)
    task = asyncio.create_task(
        server.script(
            purpose="验证脚本取消清理",
            script="sleep 5\n",
            timeout=None,
            ctx=_EmptyContext(),
        )
    )
    for _ in range(100):
        if artifact_dir.exists() and list(artifact_dir.iterdir()):
            break
        await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not artifact_dir.exists() or not list(artifact_dir.iterdir())
