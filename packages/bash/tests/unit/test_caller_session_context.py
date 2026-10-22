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


class _Context:
    def __init__(self, meta):
        self.request_context = type("RequestContext", (), {"meta": meta})()


class _EmptyContext:
    request_context = None


def _trusted_context(session_id: str, shell_env: dict[str, str] | None = None) -> _Context:
    meta = {"assistant": {"session_id": session_id}}
    if shell_env is not None:
        meta["shell_env"] = shell_env
    return _Context(meta)


def _enable_trusted_transport(monkeypatch, token: str = "test-caller-context-token") -> None:
    server.LOGGER.propagate = True
    monkeypatch.setattr(server, "TRANSPORT", "sse")
    monkeypatch.setattr(server, "CALLER_CONTEXT_TOKEN", token)
    monkeypatch.setattr(
        server,
        "_get_http_headers",
        lambda: {server.CALLER_CONTEXT_HEADER: token},
    )


def _configure_temp_dirs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server, "BASH_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(server, "BASH_OUTPUT_DIR", str(tmp_path / "outputs"))


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_mode", ["bash", "run", "script"])
async def test_execution_entries_project_trusted_caller_session_id(monkeypatch, tmp_path, tool_mode):
    _configure_temp_dirs(monkeypatch, tmp_path)
    _enable_trusted_transport(monkeypatch)
    session_id = "ses-4242"
    ctx = _trusted_context(
        session_id,
        {server.CALLER_SESSION_ID_ENV: "ses-forged", "SAFE_ENV": "preserved"},
    )

    if tool_mode == "bash":
        result = await server.bash(
            purpose="验证 Bash 调用会话环境",
            command=f'printf "%s|%s" "${server.CALLER_SESSION_ID_ENV}" "$SAFE_ENV"',
            cwd=str(tmp_path),
            ctx=ctx,
        )
    elif tool_mode == "run":
        result = await server.run(
            purpose="验证 argv 调用会话环境",
            argv=[
                sys.executable,
                "-c",
                (
                    "import os; print("
                    f"os.environ[{server.CALLER_SESSION_ID_ENV!r}] + '|' + os.environ['SAFE_ENV']"
                    ")"
                ),
            ],
            cwd=str(tmp_path),
            ctx=ctx,
        )
    else:
        result = await server.script(
            purpose="验证脚本调用会话环境",
            script=f'printf "%s|%s" "${server.CALLER_SESSION_ID_ENV}" "$SAFE_ENV"\n',
            cwd=str(tmp_path),
            ctx=ctx,
        )

    assert result.content["code"] == "OK"
    assert result.content["output"].rstrip("\n") == f"{session_id}|preserved"


@pytest.mark.asyncio
async def test_missing_or_invalid_context_clears_inherited_caller_session_id(monkeypatch, tmp_path):
    _configure_temp_dirs(monkeypatch, tmp_path)
    monkeypatch.setenv(server.CALLER_SESSION_ID_ENV, "ses-server-environment")

    missing = await server.bash(
        purpose="验证缺失调用会话上下文",
        command=f'printf %s "${server.CALLER_SESSION_ID_ENV}"',
        cwd=str(tmp_path),
        ctx=_EmptyContext(),
    )
    invalid = await server.run(
        purpose="验证非法调用会话上下文",
        argv=[sys.executable, "-c", f"import os; print(os.environ[{server.CALLER_SESSION_ID_ENV!r}])"],
        cwd=str(tmp_path),
        ctx=_trusted_context("not-a-session"),
    )

    assert missing.content["output"] == ""
    assert invalid.content["output"] == "\n"


def test_trusted_caller_session_id_requires_valid_assistant_metadata(monkeypatch):
    monkeypatch.setattr(server, "TRANSPORT", "sse")
    assert server._extract_trusted_caller_session_id(_trusted_context("ses-42")) is None


def test_stdio_transport_is_trusted_by_default(monkeypatch):
    monkeypatch.setattr(server, "TRANSPORT", "stdio")
    monkeypatch.setattr(server, "CALLER_CONTEXT_TOKEN", "")
    assert server._extract_trusted_caller_session_id(_trusted_context("ses-4242")) == "ses-4242"
    env_overrides, injected = server._build_execution_env(_trusted_context("ses-4242"))
    assert injected is True
    assert env_overrides[server.CALLER_SESSION_ID_ENV] == "ses-4242"


def test_authenticated_transport_requires_valid_assistant_metadata(monkeypatch):
    _enable_trusted_transport(monkeypatch)

    assert server._extract_trusted_caller_session_id(_trusted_context(" ses-42 ")) == "ses-42"
    assert server._extract_trusted_caller_session_id(_trusted_context("ses-evil")) is None
    assert server._extract_trusted_caller_session_id(_Context({"assistant": "ses-42"})) is None
    assert server._extract_trusted_caller_session_id(_Context({"shell_env": {"assistant": "ses-42"}})) is None


def test_wire_metadata_cannot_forge_caller_context_without_authenticated_transport(monkeypatch):
    from mcp import types

    monkeypatch.setattr(server, "TRANSPORT", "sse")
    monkeypatch.setattr(server, "CALLER_CONTEXT_TOKEN", "")
    request = types.CallToolRequestParams.model_validate(
        {
            "name": "bash",
            "arguments": {},
            "_meta": {"assistant": {"session_id": "ses-4242"}},
        }
    )

    assert server._extract_trusted_caller_session_id(_Context(request.meta)) is None
    env_overrides, injected = server._build_execution_env(_Context(request.meta))
    assert injected is False
    assert env_overrides[server.CALLER_SESSION_ID_ENV] == ""


def test_wrong_authenticated_transport_token_cannot_project_caller_session(monkeypatch):
    monkeypatch.setattr(server, "TRANSPORT", "sse")
    monkeypatch.setattr(server, "CALLER_CONTEXT_TOKEN", "expected-token")
    monkeypatch.setattr(
        server,
        "_get_http_headers",
        lambda: {server.CALLER_CONTEXT_HEADER: "forged-token"},
    )

    env_overrides, injected = server._build_execution_env(_trusted_context("ses-4242"))

    assert injected is False
    assert env_overrides[server.CALLER_SESSION_ID_ENV] == ""


@pytest.mark.asyncio
async def test_background_execution_inherits_trusted_caller_session_id(runtime, monkeypatch, tmp_path):
    _configure_temp_dirs(monkeypatch, tmp_path)
    _enable_trusted_transport(monkeypatch)
    session_id = "ses-9001"
    result = await server.bash(
        purpose="验证后台调用会话环境",
        command=f'sleep 0.05; printf %s "${server.CALLER_SESSION_ID_ENV}"',
        cwd=str(tmp_path),
        timeout=0.001,
        ctx=_trusted_context(session_id),
    )

    assert result.content["code"] == "BACKGROUND"
    completed = await runtime.get_background_result(
        pid=result.content["background_pid"],
        timeout=1,
        output_bytes=100,
    )
    assert completed["code"] == "OK"
    assert completed["output"] == session_id


@pytest.mark.asyncio
async def test_caller_session_log_records_only_name_and_status(caplog, monkeypatch):
    session_id = "ses-7331"
    _enable_trusted_transport(monkeypatch)

    async def fake_run_command(**kwargs):
        assert kwargs["env_overrides"][server.CALLER_SESSION_ID_ENV] == session_id
        return {
            "success": True,
            "code": "OK",
            "message": "ok",
            "exit_code": 0,
            "output": "",
            "output_path": None,
            "truncated": False,
            "returned_output_bytes": 0,
            "total_bytes": 0,
            "duration_ms": 0,
            "background_pid": None,
        }

    monkeypatch.setattr(server, "run_command", fake_run_command)
    with caplog.at_level("INFO", logger=server.LOGGER.name):
        result = await (
            server.bash(
                purpose="验证调用会话日志脱敏",
                command="true",
                ctx=_trusted_context(session_id),
            )
        )

    assert result.content["code"] == "OK"
    assert server.CALLER_SESSION_ID_ENV in caplog.text
    assert "injected=True" in caplog.text
    assert session_id not in caplog.text
    assert "ses-forged" not in caplog.text
