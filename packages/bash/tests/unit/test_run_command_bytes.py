import asyncio
import os
import sys
import tempfile
import time
from types import ModuleType
from unittest.mock import AsyncMock

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
        def decorator(func):
            return func
        return decorator

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

from mcp_server_bash import config, server as main, tracking
from mcp_server_bash.output_capture import read_preview_file


@pytest.mark.asyncio
async def test_run_command_uses_head_output_with_default_cap(runtime):
    result = await (
        runtime.run_command(
            command='python3 -c "print(\'abcdef\')"',
            cwd=".",
            max_output_bytes=4,
        )
    )

    assert result["success"] is True
    assert result["output"] == "abcd"
    assert result["truncated"] is True
    assert result["returned_output_bytes"] == len("abcd".encode("utf-8"))
    assert result["total_bytes"] == len("abcdef\n".encode("utf-8"))


def test_preview_returns_head_and_tail_and_keeps_full_output(tmp_path):
    path = tmp_path / "output.log"
    original = "START " + "m" * 90 + " END"
    path.write_text(original, encoding="utf-8")

    output, truncated, total_bytes = read_preview_file(str(path), 48)

    assert truncated is True
    assert output.startswith("START ")
    assert "...[output omitted]..." in output
    assert output.endswith(" END")
    assert len(output.encode("utf-8")) <= 48
    assert total_bytes == len(original)
    assert path.read_text() == original


def test_preview_preserves_complete_short_output_and_utf8_boundaries(tmp_path):
    path = tmp_path / "utf8.log"
    original = "首" * 30 + "终" * 30
    path.write_text(original, encoding="utf-8")
    output, truncated, total_bytes = read_preview_file(str(path), 54)
    assert truncated is True
    assert output.startswith("首")
    assert output.endswith("终")
    assert "�" not in output
    assert len(output.encode("utf-8")) <= 54
    assert total_bytes == len(original.encode("utf-8"))
    assert read_preview_file(str(path), total_bytes) == (original, False, total_bytes)


def test_tiny_background_preview_keeps_latest_tail(tmp_path):
    path = tmp_path / "progress.log"
    path.write_text("start-middle-finish", encoding="utf-8")
    assert read_preview_file(str(path), 6) == ("start-", True, 19)
    assert read_preview_file(str(path), 6, prefer_tail_when_small=True) == (
        "finish", True, 19
    )


@pytest.mark.asyncio
async def test_run_command_uses_head_and_tail_for_long_output(runtime):
    result = await (runtime.run_command(
        command="python3 -c 'print(\"START\" + \"x\" * 100 + \"END\")'",
        cwd=".",
        max_output_bytes=50,
    ))
    assert result["output"].startswith("START")
    assert result["output"].endswith("END\n")
    assert "...[output omitted]..." in result["output"]
    assert result["truncated"] is True
    assert result["returned_output_bytes"] <= 50


@pytest.mark.asyncio
async def test_run_command_allows_zero_bytes_and_returns_empty_output(runtime):
    result = await (
        runtime.run_command(
            command='python3 -c "print(\'abcdef\')"',
            cwd=".",
            max_output_bytes=4,
            output_bytes=0,
        )
    )

    assert result["success"] is True
    assert result["output"] == ""
    assert result["truncated"] is True
    assert result["returned_output_bytes"] == 0
    assert result["total_bytes"] == len("abcdef\n".encode("utf-8"))


@pytest.mark.asyncio
async def test_run_command_rejects_negative_bytes(runtime):
    result = await (
        runtime.run_command(
            command='python3 -c "print(\'abcdef\')"',
            cwd=".",
            max_output_bytes=4,
            output_bytes=-1,
        )
    )

    assert result["success"] is False
    assert result["code"] == "INVALID_BYTES"
    assert result["message"] == "bytes 不能小于 0"
    assert result["returned_output_bytes"] == 0
    assert result["total_bytes"] == 0


@pytest.mark.asyncio
async def test_run_command_clamps_requested_bytes_to_hard_cap(runtime):
    result = await (
        runtime.run_command(
            command='python3 -c "print(\'abcdef\')"',
            cwd=".",
            max_output_bytes=4,
            output_bytes=99,
        )
    )

    assert result["success"] is True
    assert result["output"] == "abcd"
    assert result["truncated"] is True
    assert result["returned_output_bytes"] == len("abcd".encode("utf-8"))
    assert result["total_bytes"] == len("abcdef\n".encode("utf-8"))


@pytest.mark.asyncio
async def test_run_command_returns_empty_output_when_command_is_silent(runtime):
    result = await (
        runtime.run_command(
            command='python3 -c "pass"',
            cwd=".",
            max_output_bytes=4,
            output_bytes=0,
        )
    )

    assert result["success"] is True
    assert result["output"] == ""
    assert result["truncated"] is False
    assert result["returned_output_bytes"] == 0
    assert result["total_bytes"] == 0


@pytest.mark.asyncio
async def test_run_command_nonzero_exit_is_unsuccessful(runtime):
    result = await (
        runtime.run_command(
            command="printf 'before-exit'; exit 7",
            cwd=".",
        )
    )

    assert result["success"] is False
    assert result["code"] == "COMMAND_ERROR"
    assert result["exit_code"] == 7
    assert result["output"] == "before-exit"


@pytest.mark.asyncio
async def test_get_not_running_returns_null_total_bytes_and_zero_returned_output_bytes(runtime):
    result = await (runtime.get_background_result(pid=999999, output_bytes=200))

    assert result["success"] is False
    assert result["code"] == "NOT_RUNNING"
    assert result["output"] == ""
    assert result["returned_output_bytes"] == 0
    assert result["total_bytes"] is None


def test_build_get_summary_uses_get_and_pid():
    assert main._build_get_summary(12345) == "get 12345"


@pytest.mark.asyncio
async def test_get_passes_clamped_bytes(monkeypatch):
    get_background_result = AsyncMock(
        return_value={"success": True, "code": "RUNNING", "message": "仍在运行"}
    )
    monkeypatch.setattr(main, "BASH_MAX_OUTPUT_BYTES", 128)
    monkeypatch.setattr(main, "get_background_result", get_background_result)

    result = await main.get(pid=123, timeout=2, bytes=1000)

    assert result.content["code"] == "RUNNING"
    get_background_result.assert_awaited_once_with(
        pid=123, timeout=2, output_bytes=128
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "code"),
    [({"bytes": -1}, "INVALID_BYTES"), ({"timeout": -1}, "INVALID_TIMEOUT")],
)
async def test_get_rejects_invalid_bounds(kwargs, code):
    result = await main.get(pid=123, **kwargs)

    assert result.content["success"] is False
    assert result.content["code"] == code


def test_get_tool_has_no_context_parameter():
    assert "ctx" not in main.get.__annotations__


def test_extract_invocation_id_reads_request_metadata():
    class _RequestContext:
        meta = {"invocation_id": "  mcp-123  "}

    class _Ctx:
        request_context = _RequestContext()

    assert main._extract_invocation_id(_Ctx()) == "mcp-123"


def test_extract_invocation_id_rejects_log_control_characters():
    class _RequestContext:
        meta = {"invocation_id": "mcp-valid\nforged-log"}

    class _Ctx:
        request_context = _RequestContext()

    assert main._extract_invocation_id(_Ctx()) is None


@pytest.mark.asyncio
async def test_bash_logs_invocation_lifecycle_without_command(caplog, monkeypatch):
    async def fake_run_command(**kwargs):
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
            "duration_ms": 12,
            "background_pid": None,
        }

    main.LOGGER.propagate = True
    monkeypatch.setattr(main, "run_command", fake_run_command)
    with caplog.at_level("INFO", logger=main.LOGGER.name):
        result = await (
            main.bash(
                purpose="验证 Bash 生命周期日志",
                command="echo super-secret-value",
                cwd=".",
                timeout=5,
                timeout_behavior="background",
                ctx=_FakeContext(),
            )
        )

    assert result.content["code"] == "OK"
    assert "bash call started: invocation_id=bash-" in caplog.text
    assert "bash call finished: invocation_id=bash-" in caplog.text
    assert "timeout=5" in caplog.text
    assert "code=OK" in caplog.text
    assert "super-secret-value" not in caplog.text


@pytest.mark.asyncio
async def test_bash_uses_request_invocation_id_for_lifecycle_logs(caplog, monkeypatch):
    class _RequestContext:
        meta = {"invocation_id": "mcp-shared-123"}

    class _Ctx:
        request_context = _RequestContext()

    async def fake_run_command(**kwargs):
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
        }

    main.LOGGER.propagate = True
    monkeypatch.setattr(main, "run_command", fake_run_command)
    with caplog.at_level("INFO", logger=main.LOGGER.name):
        result = await (
            main.bash(purpose="验证调用标识日志", command="echo hi", ctx=_Ctx())
        )

    assert result.content["code"] == "OK"
    assert "bash call started: invocation_id=mcp-shared-123" in caplog.text
    assert "bash call finished: invocation_id=mcp-shared-123" in caplog.text


def test_resolve_execution_target_uses_local_default_cwd(monkeypatch):
    monkeypatch.setattr(main, "DEFAULT_CWD", "/task/default")

    assert main._resolve_execution_target(None).cwd == "/task/default"
    assert main._resolve_execution_target("/task/explicit").cwd == "/task/explicit"


def test_parse_bool_env_accepts_true_and_default_false():
    assert config.parse_bool_env("true") is True
    assert config.parse_bool_env(None, default=False) is False


def test_parse_int_env_applies_min_value_and_default():
    assert config.parse_int_env("-1", default=5, min_value=0) == 0
    assert config.parse_int_env("oops", default=5, min_value=0) == 5


def test_load_env_config_reads_project_root_dotenv(tmp_path):
    (tmp_path / ".env").write_text("A=1\n# comment\nB = two\n", encoding="utf-8")

    loaded = config.load_env_config(tmp_path)

    assert loaded == {"A": "1", "B": "two"}


def test_process_env_overrides_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DEFAULT_CWD=/from/file\n", encoding="utf-8")
    monkeypatch.setenv("DEFAULT_CWD", "/from/env")

    assert config.load_env_config(tmp_path)["DEFAULT_CWD"] == "/from/env"


def test_process_env_supplies_config_without_dotenv(tmp_path, monkeypatch):
    """An installed wheel cannot reach the source .env; env must be enough."""
    monkeypatch.setenv("DEFAULT_CWD", "/env-only")
    monkeypatch.setenv("BASH_OUTPUT_DIR", "/env/out")

    loaded = config.load_env_config(tmp_path)

    assert loaded == {"DEFAULT_CWD": "/env-only", "BASH_OUTPUT_DIR": "/env/out"}
    assert config.load_app_config(tmp_path).default_cwd == "/env-only"


def test_process_env_outside_whitelist_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", "/not/a/config/key")
    monkeypatch.setenv("RANDOM_UNRELATED", "x")

    assert config.load_env_config(tmp_path) == {}


def test_config_reading_does_not_pollute_os_environ(tmp_path, monkeypatch):
    """The execution backend inherits os.environ for every command."""
    varname = "BASH_CALLER_CONTEXT_TOKEN"
    monkeypatch.delenv(varname, raising=False)
    (tmp_path / ".env").write_text(f"{varname}=secret-token\n", encoding="utf-8")

    assert config.load_env_config(tmp_path)[varname] == "secret-token"
    assert varname not in os.environ


@pytest.mark.asyncio
async def test_get_background_result_returns_final_result_without_consuming_finished_process(runtime, monkeypatch):
    task = asyncio.get_running_loop().create_future()
    task.set_result(0)
    process = tracking.BackgroundProcess(
        pid=123,
        display_command="echo hi",
        output_path="/tmp/nope",
        start_time=time.monotonic(),
        task=task,
        tool_mode="bash",
    )
    runtime._tracker.processes.update({123: process})

    result = await runtime.get_background_result(pid=123, output_bytes=50)

    assert result["code"] == "OK"
    assert result["exit_code"] == 0
    assert runtime._tracker.processes == {123: process}


@pytest.mark.asyncio
async def test_get_background_result_can_read_finished_process_repeatedly(runtime, monkeypatch, tmp_path):
    output_path = tmp_path / "repeat.log"
    output_path.write_text("repeat-output", encoding="utf-8")
    task = asyncio.get_running_loop().create_future()
    task.set_result(0)
    process = tracking.BackgroundProcess(
        pid=124,
        display_command="echo repeat",
        output_path=str(output_path),
        start_time=time.monotonic(),
        task=task,
        tool_mode="bash",
    )
    runtime._tracker.processes.update({124: process})

    first = await runtime.get_background_result(pid=124, output_bytes=50)
    second = await runtime.get_background_result(pid=124, output_bytes=50)

    assert first["code"] == "OK"
    assert second["code"] == "OK"
    assert first["output"] == "repeat-output"
    assert second["output"] == "repeat-output"
    assert runtime._tracker.processes == {124: process}


@pytest.mark.asyncio
async def test_get_background_result_returns_running_when_wait_timeout_expires(runtime, monkeypatch, tmp_path):
    output_path = tmp_path / "running.log"
    output_path.write_text("partial", encoding="utf-8")
    task = asyncio.get_running_loop().create_future()
    process = tracking.BackgroundProcess(
        pid=125,
        display_command="sleep 1",
        output_path=str(output_path),
        start_time=time.monotonic(),
        task=task,
        tool_mode="bash",
    )
    runtime._tracker.processes.update({125: process})

    result = await runtime.get_background_result(pid=125, timeout=0.01, output_bytes=50)

    assert result["code"] == "RUNNING"
    assert result["background_pid"] == 125
    assert not task.done()


@pytest.mark.asyncio
async def test_completed_process_retention_cleanup_removes_record(runtime, monkeypatch, tmp_path):
    output_path = tmp_path / "retained.log"
    output_path.write_text("done", encoding="utf-8")
    task = asyncio.get_running_loop().create_future()
    task.set_result(0)
    process = tracking.BackgroundProcess(
        pid=126,
        display_command="echo done",
        output_path=str(output_path),
        start_time=time.monotonic(),
        task=task,
        tool_mode="bash",
    )
    processes = runtime._tracker.processes
    processes[126] = process
    monkeypatch.setattr(runtime._tracker, "retention_seconds", 0)

    runtime._tracker.schedule_cleanup(process)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert processes == {}


@pytest.mark.asyncio
async def test_get_background_result_returns_empty_output_for_silent_process(runtime, monkeypatch, tmp_path):
    output_path = tmp_path / "silent.log"
    output_path.touch()
    task = asyncio.get_running_loop().create_future()
    task.set_result(0)
    process = tracking.BackgroundProcess(
        pid=457,
        display_command="silent command",
        output_path=str(output_path),
        start_time=time.monotonic(),
        task=task,
        tool_mode="bash",
    )
    runtime._tracker.processes.update({457: process})

    result = await runtime.get_background_result(pid=457, output_bytes=50)

    assert result["success"] is True
    assert result["code"] == "OK"
    assert result["output"] == ""
    assert result["returned_output_bytes"] == 0
    assert result["total_bytes"] == 0


@pytest.mark.asyncio
async def test_get_background_result_returns_command_error_after_completion(runtime, monkeypatch):
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as f:
        f.write("tail-output")
        output_path = f.name

    task = asyncio.get_running_loop().create_future()

    async def complete_later():
        await asyncio.sleep(0)
        task.set_result(7)

    asyncio.create_task(complete_later())
    process = tracking.BackgroundProcess(
        pid=456,
        display_command="bad command",
        output_path=output_path,
        start_time=time.monotonic(),
        task=task,
        tool_mode="bash",
    )
    runtime._tracker.processes.update({456: process})

    result = await runtime.get_background_result(pid=456, timeout=1, output_bytes=50)

    assert result["success"] is False
    assert result["code"] == "COMMAND_ERROR"
    assert result["exit_code"] == 7
    assert result["output"] == "tail-output"
    assert runtime._tracker.processes == {456: process}


@pytest.mark.asyncio
async def test_bash_returns_invalid_meta_tool_result(monkeypatch):
    class _RequestContext:
        def __init__(self):
            self.meta = {"shell_env": ["bad"]}

    class _Ctx:
        request_context = _RequestContext()

    monkeypatch.setattr(main, "run_command", AsyncMock())

    result = await main.bash(
        purpose="验证非法调用 metadata",
        command="echo hi",
        ctx=_Ctx(),
    )

    assert result.content["code"] == "INVALID_META"
    assert result.content["returned_output_bytes"] == 0
    assert result.content["total_bytes"] is None
    assert result.meta == {"summary": "验证非法调用 metadata"}
