import json
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

from mcp_server_bash import server
from mcp_server_bash.models import ExecutionRequest, ExecutionTarget


def test_execution_request_rejects_inconsistent_payload():
    target = ExecutionTarget(cwd="/tmp")

    with pytest.raises(ValueError):
        ExecutionRequest(
            tool_mode="run",
            command="echo bad",
            argv=("echo",),
            script_path=None,
            stdin_path=None,
            env_overrides={},
            target=target,
        )


def test_execution_request_rejects_unknown_tool_mode():
    target = ExecutionTarget(cwd="/tmp")

    with pytest.raises(ValueError, match="unsupported tool_mode"):
        ExecutionRequest(
            tool_mode="unknown",  # type: ignore[arg-type]
            command=None,
            argv=None,
            script_path="/tmp/script.sh",
            stdin_path=None,
            env_overrides={},
            target=target,
        )


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        ([], "INVALID_ARGV"),
        ([""], "INVALID_ARGV"),
        (["echo", 1], "INVALID_ARGV"),
        (["echo", "bad\0arg"], "INVALID_ARGV"),
    ],
)
@pytest.mark.asyncio
async def test_run_rejects_invalid_argv_without_starting(monkeypatch, argv, code):
    run_argv = pytest.importorskip("unittest.mock").AsyncMock()
    monkeypatch.setattr(server, "run_argv", run_argv)

    result = await (server.run(purpose="验证非法 argv", argv=argv))

    assert result.content["code"] == code
    assert result.content["tool_mode"] == "run"
    run_argv.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_rejects_argv_limits(monkeypatch):
    monkeypatch.setattr(server, "BASH_MAX_ARGV_COUNT", 1)

    result = await (server.run(purpose="验证 argv 数量限制", argv=["echo", "extra"]))

    assert result.content["code"] == "ARGV_TOO_LARGE"


@pytest.mark.asyncio
async def test_local_run_preserves_argv_boundaries_and_shell_literals(runtime, tmp_path):
    payload = [
        "",
        "space value",
        "$HOME",
        "$(printf injected)",
        "`printf injected`",
        "; echo injected",
        "a|b",
        "中文",
        "line1\nline2",
    ]
    code = "import json,sys; print(json.dumps(sys.argv[1:], ensure_ascii=False))"

    result = await runtime.run_argv(
        argv=(sys.executable, "-c", code, *payload),
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
    )

    assert result["code"] == "OK"
    assert result["tool_mode"] == "run"
    assert json.loads(result["output"]) == payload


@pytest.mark.asyncio
async def test_local_run_receives_env_overrides(runtime, tmp_path):
    result = await runtime.run_argv(
        argv=(sys.executable, "-c", "import os; print(os.environ['RUN_TEST_ENV'])"),
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        env_overrides={"RUN_TEST_ENV": "visible"},
    )

    assert result["code"] == "OK"
    assert result["output"].strip() == "visible"


@pytest.mark.asyncio
async def test_local_run_background_get_retains_tool_mode(runtime, monkeypatch, tmp_path):

    result = await runtime.run_argv(
        argv=(sys.executable, "-c", "import time; time.sleep(0.1); print('done')"),
        cwd=str(tmp_path),
        timeout=0.01,
        output_dir=str(tmp_path),
    )

    assert result["code"] == "BACKGROUND"
    assert result["tool_mode"] == "run"
    finished = await runtime.get_background_result(
        pid=result["background_pid"],
        timeout=1,
        output_bytes=100,
    )
    assert finished["code"] == "OK"
    assert finished["tool_mode"] == "run"
