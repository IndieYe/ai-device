from __future__ import annotations

import json
import subprocess
import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock

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


class _RequestContext:
    meta = None


class _EmptyContext:
    request_context = _RequestContext()


def _completed_result(tool_mode: str) -> dict[str, object]:
    return {
        "success": True,
        "code": "OK",
        "message": "命令执行成功",
        "exit_code": 0,
        "output": "",
        "output_path": "/tmp/output.log",
        "truncated": False,
        "returned_output_bytes": 0,
        "total_bytes": 0,
        "duration_ms": 1,
        "background_pid": None,
        "tool_mode": tool_mode,
    }


def test_execution_tool_schemas_make_purpose_optional_and_declare_summary_key():
    code = """
import asyncio
import json
from mcp_server_bash import server

async def main():
    result = {}
    for name in ("bash", "run", "script", "get"):
        tool = await server.mcp.get_tool(name)
        result[name] = {
            "required": tool.parameters.get("required", []),
            "properties": tool.parameters.get("properties", {}),
            "annotations": tool.annotations.model_dump(exclude_none=True) if tool.annotations else {},
        }
    print(json.dumps(result))

asyncio.run(main())
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    tools = json.loads(completed.stdout)

    for tool_name in ("bash", "run", "script"):
        assert "purpose" not in tools[tool_name]["required"]
        assert tools[tool_name]["properties"]["purpose"]["type"] == "string"
        assert tools[tool_name]["annotations"]["summary_key"] == "purpose"
    assert "purpose" not in tools["get"]["properties"]
    assert "summary_key" not in tools["get"]["annotations"]


def test_real_fastmcp_calls_return_invalid_purpose_for_non_string_values():
    code = """
import asyncio
import json
from fastmcp import Client
from mcp_server_bash import server

async def main():
    cases = {
        "bash": {"purpose": 123, "command": "printf should-not-run"},
        "run": {"purpose": {"why": "test"}, "argv": ["printf", "should-not-run"]},
        "script": {"purpose": ["test"], "script": "printf should-not-run\\n"},
    }
    result = {}
    async with Client(server.mcp) as client:
        for name, arguments in cases.items():
            tool_result = await client.call_tool(name, arguments)
            result[name] = {
                "content": json.loads(tool_result.content[0].text),
                "meta": tool_result.meta,
            }
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(main())
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    results = json.loads(completed.stdout)

    assert results["bash"]["content"]["code"] == "INVALID_PURPOSE"
    assert results["bash"]["meta"] == {"summary": "bash"}
    assert results["run"]["content"]["code"] == "INVALID_PURPOSE"
    assert results["run"]["meta"] == {"summary": "run command"}
    assert results["script"]["content"]["code"] == "INVALID_PURPOSE"
    assert results["script"]["meta"] == {"summary": "script"}


@pytest.mark.parametrize(
    ("value", "normalized", "error"),
    [
        ("  检查  后端测试  ", "检查  后端测试", None),
        ("中" * server.PURPOSE_MAX_CHARS, "中" * server.PURPOSE_MAX_CHARS, None),
        ("", None, "purpose 不能为空"),
        (" \n\t ", None, "purpose 不能为空"),
        (None, None, None),
        ("中" * (server.PURPOSE_MAX_CHARS + 1), None, "purpose 不能超过 200 个字符"),
    ],
)
def test_normalize_purpose(value, normalized, error):
    assert server._normalize_purpose(value) == (normalized, error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "kwargs", "fallback_summary"),
    [
        ("bash", {"command": "printf should-not-run"}, "bash"),
        ("run", {"argv": ["printf", "should-not-run"]}, "run command"),
        ("script", {"script": "printf should-not-run\n"}, "script"),
    ],
)
async def test_invalid_purpose_rejects_before_execution_or_artifact_creation(
    monkeypatch,
    tool_name,
    kwargs,
    fallback_summary,
):
    run_command = AsyncMock()
    run_argv = AsyncMock()
    run_script = AsyncMock()
    create_stdin_artifact = Mock()
    create_script_artifacts = Mock()
    monkeypatch.setattr(server, "run_command", run_command)
    monkeypatch.setattr(server, "run_argv", run_argv)
    monkeypatch.setattr(server, "run_script", run_script)
    monkeypatch.setattr(server, "create_stdin_artifact", create_stdin_artifact)
    monkeypatch.setattr(server, "create_script_artifacts", create_script_artifacts)

    tool = getattr(server, tool_name)
    result = await tool(purpose=" \n\t ", ctx=_EmptyContext(), **kwargs)

    assert result.content["code"] == "INVALID_PURPOSE"
    assert result.content["exit_code"] == -1
    assert result.content["background_pid"] is None
    assert result.meta == {"summary": fallback_summary}
    run_command.assert_not_awaited()
    run_argv.assert_not_awaited()
    run_script.assert_not_awaited()
    create_stdin_artifact.assert_not_called()
    create_script_artifacts.assert_not_called()


@pytest.mark.asyncio
async def test_omitted_purpose_uses_stable_tool_summary(monkeypatch):
    monkeypatch.setattr(server, "run_command", AsyncMock(return_value=_completed_result("bash")))
    monkeypatch.setattr(server, "run_argv", AsyncMock(return_value=_completed_result("run")))
    monkeypatch.setattr(server, "run_script", AsyncMock(return_value=_completed_result("script")))

    bash_result = await server.bash(command="printf ok", ctx=_EmptyContext())
    run_result = await server.run(argv=["printf", "ok"], ctx=_EmptyContext())
    script_result = await server.script(script="printf ok\n", ctx=_EmptyContext())

    assert bash_result.meta == {"summary": "bash"}
    assert run_result.meta == {"summary": "run command"}
    assert script_result.meta == {"summary": "script"}


@pytest.mark.asyncio
async def test_bash_uses_normalized_purpose_without_passing_it_to_executor(monkeypatch):
    captured = {}

    async def fake_run_command(**kwargs):
        captured.update(kwargs)
        return _completed_result("bash")

    monkeypatch.setattr(server, "run_command", fake_run_command)

    result = await server.bash(
        purpose="  检查  Bash 执行  ",
        command="printf ok",
        ctx=_EmptyContext(),
    )

    assert result.content["code"] == "OK"
    assert result.meta == {"summary": "检查  Bash 执行"}
    assert "purpose" not in captured
    assert captured["command"] == "printf ok"
    assert captured["target"].cwd == captured["cwd"]


@pytest.mark.asyncio
async def test_run_uses_normalized_purpose_without_passing_it_to_executor(monkeypatch):
    captured = {}

    async def fake_run_argv(**kwargs):
        captured.update(kwargs)
        return _completed_result("run")

    monkeypatch.setattr(server, "run_argv", fake_run_argv)

    result = await server.run(
        purpose="  检查结构化参数边界  ",
        argv=["printf", "%s", "ok"],
        ctx=_EmptyContext(),
    )

    assert result.content["code"] == "OK"
    assert result.meta == {"summary": "检查结构化参数边界"}
    assert "purpose" not in captured
    assert captured["argv"] == ("printf", "%s", "ok")
    assert captured["target"].cwd == captured["cwd"]


@pytest.mark.asyncio
async def test_later_validation_errors_keep_valid_purpose():
    run_result = await server.run(
        purpose="验证 argv 输入",
        argv=[],
        ctx=_EmptyContext(),
    )
    script_result = await server.script(
        purpose="验证脚本输入",
        script="",
        ctx=_EmptyContext(),
    )

    assert run_result.content["code"] == "INVALID_ARGV"
    assert run_result.meta == {"summary": "验证 argv 输入"}
    assert script_result.content["code"] == "INVALID_SCRIPT"
    assert script_result.meta == {"summary": "验证脚本输入"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "executor_name", "kwargs", "tool_mode", "code"),
    [
        ("bash", "run_command", {"command": "exit 2"}, "bash", "COMMAND_ERROR"),
        ("run", "run_argv", {"argv": ["sleep", "10"]}, "run", "TIMEOUT"),
        ("script", "run_script", {"script": "sleep 10\n"}, "script", "BACKGROUND"),
    ],
)
async def test_executor_terminal_results_keep_valid_purpose(
    monkeypatch,
    tool_name,
    executor_name,
    kwargs,
    tool_mode,
    code,
):
    result_content = _completed_result(tool_mode)
    result_content.update(
        success=False,
        code=code,
        message="representative terminal result",
        exit_code=2,
    )
    monkeypatch.setattr(server, executor_name, AsyncMock(return_value=result_content))

    result = await getattr(server, tool_name)(
        purpose="验证失败终态摘要",
        ctx=_EmptyContext(),
        **kwargs,
    )

    assert result.content["code"] == code
    assert result.meta == {"summary": "验证失败终态摘要"}


@pytest.mark.asyncio
async def test_artifact_prepare_error_keeps_valid_purpose(monkeypatch):
    monkeypatch.setattr(
        server,
        "create_stdin_artifact",
        Mock(side_effect=OSError("isolated artifact failure")),
    )

    result = await server.run(
        purpose="验证临时资源失败摘要",
        argv=["cat"],
        stdin="input",
        ctx=_EmptyContext(),
    )

    assert result.content["code"] == "ARTIFACT_PREPARE_ERROR"
    assert result.meta == {"summary": "验证临时资源失败摘要"}
