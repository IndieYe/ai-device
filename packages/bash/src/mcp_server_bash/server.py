"""FastMCP bash command execution server."""

from collections.abc import Mapping
from contextlib import asynccontextmanager
import hmac
import os
import re
import time
import uuid
from typing import Annotated, Any, Literal, Optional

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import WithJsonSchema

from .artifacts import (
    cleanup_artifacts,
    cleanup_expired_artifacts,
    create_script_artifacts,
    create_stdin_artifact,
)

from .config import (
    PROJECT_ROOT,
    load_app_config,
    setup_server_logger,
)
from .models import ExecutionTarget
from .backends.local import get_shell_config
from .output_capture import cleanup_expired_outputs
from .core import ExecutionRuntime
from .shell import is_valid_env_var_name


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_CONFIG = load_app_config(PROJECT_ROOT)
ENV_CONFIG = APP_CONFIG.env_config
TRANSPORT = APP_CONFIG.transport
SSE_PORT = APP_CONFIG.sse_port
SSE_HOST = APP_CONFIG.sse_host
DEFAULT_CWD = APP_CONFIG.default_cwd
BASH_MAX_OUTPUT_BYTES = APP_CONFIG.bash_max_output_bytes
BASH_MAX_ARGV_COUNT = APP_CONFIG.bash_max_argv_count
BASH_MAX_ARGV_BYTES = APP_CONFIG.bash_max_argv_bytes
BASH_MAX_SCRIPT_BYTES = APP_CONFIG.bash_max_script_bytes
BASH_MAX_STDIN_BYTES = APP_CONFIG.bash_max_stdin_bytes
BASH_ARTIFACT_DIR = APP_CONFIG.bash_artifact_dir
BASH_ARTIFACT_TTL_SECONDS = APP_CONFIG.bash_artifact_ttl_seconds
BASH_OUTPUT_DIR = APP_CONFIG.bash_output_dir
BASH_OUTPUT_TTL_SECONDS = APP_CONFIG.bash_output_ttl_seconds
BASH_MAX_OUTPUT_FILE_BYTES = APP_CONFIG.bash_max_output_file_bytes
SERVER_LOG_ENABLED = APP_CONFIG.server_log_enabled
SERVER_LOG_LEVEL = APP_CONFIG.server_log_level
SERVER_LOG_DIR = APP_CONFIG.server_log_dir

LOGGER, SERVER_LOG_FILE = setup_server_logger(
    enabled=SERVER_LOG_ENABLED,
    level=SERVER_LOG_LEVEL,
    log_dir=SERVER_LOG_DIR,
)


# ---------------------------------------------------------------------------
# FastMCP App
# ---------------------------------------------------------------------------

RUNTIME = ExecutionRuntime()


async def run_command(*args, **kwargs):
    return await RUNTIME.run_command(*args, **kwargs)


async def run_argv(*args, **kwargs):
    return await RUNTIME.run_argv(*args, **kwargs)


async def run_script(*args, **kwargs):
    return await RUNTIME.run_script(*args, **kwargs)


async def get_background_result(*args, **kwargs):
    return await RUNTIME.get_background_result(*args, **kwargs)


@asynccontextmanager
async def _execution_lifespan(app):
    global RUNTIME
    runtime = ExecutionRuntime()
    RUNTIME = runtime
    try:
        yield
    finally:
        await runtime.aclose()


mcp = FastMCP("bash", lifespan=_execution_lifespan)
INVOCATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SESSION_ID_RE = re.compile(r"^ses-[0-9]+$")
CALLER_SESSION_ID_ENV = "AI_ASSISTANT_CALLER_SESSION_ID"
CALLER_CONTEXT_HEADER = "x-ai-assistant-caller-context"
CALLER_CONTEXT_TOKEN = str(ENV_CONFIG.get("BASH_CALLER_CONTEXT_TOKEN") or "").strip()
PURPOSE_MAX_CHARS = 200
PurposeInput = Annotated[Any, WithJsonSchema({"type": "string"})]




def _normalize_purpose(purpose: Any) -> tuple[Optional[str], Optional[str]]:
    if purpose is None:
        return None, None
    if not isinstance(purpose, str):
        return None, "purpose 必须是字符串"
    normalized = purpose.strip()
    if not normalized:
        return None, "purpose 不能为空"
    if len(normalized) > PURPOSE_MAX_CHARS:
        return None, f"purpose 不能超过 {PURPOSE_MAX_CHARS} 个字符"
    return normalized, None


def _build_get_summary(pid: int) -> str:
    return f"get {pid}"


def _error_tool_result(
    *,
    code: str,
    message: str,
    exit_code: Optional[int],
    summary: str,
    tool_mode: Optional[Literal["bash", "run", "script"]] = None,
) -> ToolResult:
    return ToolResult(
        content={
            "success": False,
            "code": code,
            "message": message,
            "exit_code": exit_code,
            "output": "",
            "output_path": None,
            "truncated": False,
            "returned_output_bytes": 0,
            "total_bytes": None,
            "duration_ms": 0,
            "background_pid": None,
            "tool_mode": tool_mode,
        },
        meta={"summary": summary},
    )


def _meta_to_plain_dict(value: Any) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped

    items = getattr(value, "items", None)
    if callable(items):
        try:
            return dict(items())
        except Exception:
            pass

    value_dict = getattr(value, "__dict__", None)
    if isinstance(value_dict, dict):
        return {
            key: item
            for key, item in value_dict.items()
            if not key.startswith("_")
        }

    return None


def _extract_shell_env_overrides(ctx: Optional[Context]) -> dict[str, str]:
    if ctx is None or ctx.request_context is None:
        return {}

    meta = ctx.request_context.meta
    if meta is None:
        return {}

    raw_shell_env = getattr(meta, "shell_env", None)
    if raw_shell_env is None:
        meta_dict = _meta_to_plain_dict(meta)
        if meta_dict is None:
            return {}
        raw_shell_env = meta_dict.get("shell_env")

    if raw_shell_env is None:
        return {}

    if not isinstance(raw_shell_env, Mapping):
        raise ValueError("meta.shell_env 必须是对象（key-value map）")

    env_overrides: dict[str, str] = {}
    for key, value in raw_shell_env.items():
        if not isinstance(key, str) or not is_valid_env_var_name(key):
            raise ValueError(f"meta.shell_env 包含非法环境变量名: {key!r}")
        if value is None:
            raise ValueError(f"meta.shell_env[{key!r}] 不能为 null")
        env_overrides[key] = str(value)

    return env_overrides


def _get_http_headers() -> Mapping[str, str] | None:
    """Return current HTTP headers without making stdio calls look authenticated."""
    try:
        from fastmcp.server.dependencies import get_http_headers

        return get_http_headers()
    except (ImportError, RuntimeError):
        return None


def _has_trusted_caller_context_transport() -> bool:
    """Require the local service-to-service secret before trusting wire metadata."""
    if TRANSPORT == "stdio":
        return True
    if not CALLER_CONTEXT_TOKEN:
        return False
    headers = _get_http_headers()
    if headers is None:
        return False
    supplied_token = headers.get(CALLER_CONTEXT_HEADER)
    return isinstance(supplied_token, str) and hmac.compare_digest(
        supplied_token,
        CALLER_CONTEXT_TOKEN,
    )


def _extract_trusted_caller_session_id(ctx: Optional[Context]) -> Optional[str]:
    """Read session metadata only after authenticating the HTTP MCP caller."""
    if not _has_trusted_caller_context_transport():
        return None
    if ctx is None or ctx.request_context is None:
        return None
    meta_dict = _meta_to_plain_dict(ctx.request_context.meta)
    if meta_dict is None:
        return None
    assistant_meta = meta_dict.get("assistant")
    if not isinstance(assistant_meta, Mapping):
        return None
    session_id = assistant_meta.get("session_id")
    if not isinstance(session_id, str):
        return None
    normalized = session_id.strip()
    return normalized if SESSION_ID_RE.fullmatch(normalized) else None


def _build_execution_env(ctx: Optional[Context]) -> tuple[dict[str, str], bool]:
    """Merge caller env with the reserved, per-invocation session projection."""
    env_overrides = _extract_shell_env_overrides(ctx)
    caller_session_id = _extract_trusted_caller_session_id(ctx)
    # Always set this key, including to an empty value, so neither shell_env nor
    # the server process environment can leak a caller context across invocations.
    env_overrides[CALLER_SESSION_ID_ENV] = caller_session_id or ""
    return env_overrides, caller_session_id is not None


def _log_execution_env(
    *,
    tool_mode: str,
    invocation_id: str,
    env_overrides: Mapping[str, str],
    caller_session_injected: bool,
) -> None:
    user_keys = sorted(key for key in env_overrides if key != CALLER_SESSION_ID_ENV)
    if user_keys:
        LOGGER.info(
            "%s call env overrides: invocation_id=%s count=%s keys=%s",
            tool_mode,
            invocation_id,
            len(user_keys),
            ", ".join(user_keys),
        )
    LOGGER.info(
        "%s caller session context: invocation_id=%s env=%s injected=%s",
        tool_mode,
        invocation_id,
        CALLER_SESSION_ID_ENV,
        caller_session_injected,
    )


def _extract_invocation_id(ctx: Optional[Context]) -> Optional[str]:
    if ctx is None or ctx.request_context is None:
        return None
    meta_dict = _meta_to_plain_dict(ctx.request_context.meta)
    if meta_dict is None:
        return None
    invocation_id = str(meta_dict.get("invocation_id") or "").strip()
    return invocation_id if INVOCATION_ID_RE.fullmatch(invocation_id) else None


def _resolve_execution_target(cwd: Optional[str]) -> ExecutionTarget:
    return ExecutionTarget(cwd=cwd or DEFAULT_CWD or os.getcwd())


def _validate_argv(argv: Any) -> Optional[tuple[str, str]]:
    if not isinstance(argv, list) or not argv:
        return "INVALID_ARGV", "argv 必须是非空字符串数组"
    if not all(isinstance(item, str) for item in argv):
        return "INVALID_ARGV", "argv 的每个元素都必须是字符串"
    if not argv[0]:
        return "INVALID_ARGV", "argv[0] 必须是非空字符串"
    if any("\0" in item for item in argv):
        return "INVALID_ARGV", "argv 元素不能包含空字节"
    if len(argv) > BASH_MAX_ARGV_COUNT:
        return "ARGV_TOO_LARGE", f"argv 参数数量不能超过 {BASH_MAX_ARGV_COUNT}"
    total_bytes = sum(len(item.encode("utf-8")) for item in argv)
    if total_bytes > BASH_MAX_ARGV_BYTES:
        return "ARGV_TOO_LARGE", f"argv UTF-8 总字节数不能超过 {BASH_MAX_ARGV_BYTES}"
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations={"summary_key": "purpose"})
async def bash(
    command: str,
    purpose: PurposeInput = None,
    cwd: Optional[str] = None,
    timeout: Optional[float] = 60,
    bytes: Optional[int] = None,
    timeout_behavior: Literal["background", "kill"] = "background",
    ctx: Context = CurrentContext(),
) -> ToolResult:
    """Execute a bash command and return the output.

    ``purpose`` is an optional short user-facing description of why the command
    is run. Provide it for most calls; it does not change or authorize the
    execution. Do not repeat the command or include sensitive payloads.

    Executes on the machine running this server. Long-running commands can
    continue in the background and be polled with ``get``.
    """
    normalized_purpose, purpose_error = _normalize_purpose(purpose)
    if purpose_error is not None:
        return _error_tool_result(
            code="INVALID_PURPOSE",
            message=purpose_error,
            exit_code=-1,
            summary="bash",
            tool_mode="bash",
        )

    summary = normalized_purpose or "bash"
    target = _resolve_execution_target(cwd)

    try:
        env_overrides, caller_session_injected = _build_execution_env(ctx)
    except ValueError as exc:
        return _error_tool_result(
            code="INVALID_META",
            message=str(exc),
            exit_code=-1,
            summary=summary,
            tool_mode="bash",
        )

    invocation_id = _extract_invocation_id(ctx) or f"bash-{uuid.uuid4().hex[:12]}"
    started_at = time.monotonic()
    LOGGER.info(
        "bash call started: invocation_id=%s timeout=%s timeout_behavior=%s cwd=%s",
        invocation_id,
        timeout,
        timeout_behavior,
        target.cwd,
    )
    _log_execution_env(
        tool_mode="bash",
        invocation_id=invocation_id,
        env_overrides=env_overrides,
        caller_session_injected=caller_session_injected,
    )

    try:
        result = await run_command(
            command=command,
            cwd=target.cwd,
            timeout=timeout,
            max_output_bytes=BASH_MAX_OUTPUT_BYTES,
            output_bytes=bytes,
            timeout_behavior=timeout_behavior,
            output_dir=BASH_OUTPUT_DIR,
            max_output_file_bytes=BASH_MAX_OUTPUT_FILE_BYTES,
            env_overrides=env_overrides,
            target=target,
        )
    except BaseException:
        LOGGER.exception(
            "bash call failed: invocation_id=%s elapsed_ms=%s",
            invocation_id,
            int((time.monotonic() - started_at) * 1000),
        )
        raise

    LOGGER.info(
        "bash call finished: invocation_id=%s code=%s exit_code=%s background_pid=%s duration_ms=%s elapsed_ms=%s",
        invocation_id,
        result.get("code"),
        result.get("exit_code"),
        result.get("background_pid"),
        result.get("duration_ms"),
        int((time.monotonic() - started_at) * 1000),
    )
    return ToolResult(content=result, meta={"summary": summary})


@mcp.tool(annotations={"summary_key": "purpose"})
async def run(
    argv: list[str],
    purpose: PurposeInput = None,
    stdin: Optional[str] = None,
    cwd: Optional[str] = None,
    timeout: Optional[float] = 60,
    bytes: Optional[int] = None,
    timeout_behavior: Literal["background", "kill"] = "background",
    ctx: Context = CurrentContext(),
) -> ToolResult:
    """Run a program with structured arguments without shell parsing.

    ``purpose`` is an optional short user-facing description of why the program
    is run. Provide it for most calls; it does not change or authorize the
    execution. Do not repeat the argv or include sensitive payloads.

    Each item in ``argv`` is passed as one process argument. Shell expansions,
    pipelines, redirects, and command substitutions are not interpreted. Use
    ``stdin`` for text input. Execution on the machine running this server preserves argv boundaries.
    """
    normalized_purpose, purpose_error = _normalize_purpose(purpose)
    if purpose_error is not None:
        return _error_tool_result(
            code="INVALID_PURPOSE",
            message=purpose_error,
            exit_code=-1,
            summary="run command",
            tool_mode="run",
        )

    summary = normalized_purpose or "run command"
    target = _resolve_execution_target(cwd)
    validation_error = _validate_argv(argv)
    if validation_error is not None:
        code, message = validation_error
        return _error_tool_result(
            code=code,
            message=message,
            exit_code=-1,
            summary=summary,
            tool_mode="run",
        )
    if stdin is not None and not isinstance(stdin, str):
        return _error_tool_result(
            code="INVALID_STDIN",
            message="stdin 必须是字符串或 null",
            exit_code=-1,
            summary=summary,
            tool_mode="run",
        )
    stdin_bytes = stdin.encode("utf-8") if stdin is not None else None
    if stdin_bytes is not None and len(stdin_bytes) > BASH_MAX_STDIN_BYTES:
        return _error_tool_result(
            code="STDIN_TOO_LARGE",
            message=f"stdin UTF-8 字节数不能超过 {BASH_MAX_STDIN_BYTES}",
            exit_code=-1,
            summary=summary,
            tool_mode="run",
        )

    try:
        env_overrides, caller_session_injected = _build_execution_env(ctx)
    except ValueError as exc:
        return _error_tool_result(
            code="INVALID_META",
            message=str(exc),
            exit_code=-1,
            summary=summary,
            tool_mode="run",
        )

    invocation_id = _extract_invocation_id(ctx) or f"run-{uuid.uuid4().hex[:12]}"
    started_at = time.monotonic()
    LOGGER.info(
        "run call started: invocation_id=%s argc=%s stdin_bytes=%s timeout=%s timeout_behavior=%s cwd=%s",
        invocation_id,
        len(argv),
        len(stdin_bytes) if stdin_bytes is not None else 0,
        timeout,
        timeout_behavior,
        target.cwd,
    )
    _log_execution_env(
        tool_mode="run",
        invocation_id=invocation_id,
        env_overrides=env_overrides,
        caller_session_injected=caller_session_injected,
    )

    stdin_path = None
    artifacts = None
    if stdin_bytes is not None:
        try:
            stdin_path, artifacts = create_stdin_artifact(stdin_bytes, BASH_ARTIFACT_DIR)
        except Exception:
            LOGGER.exception(
                "run stdin prepare failed: invocation_id=%s stdin_bytes=%s",
                invocation_id,
                        len(stdin_bytes),
            )
            return _error_tool_result(
                code="ARTIFACT_PREPARE_ERROR",
                message="无法准备 stdin 临时资源",
                exit_code=-1,
                summary=summary,
                tool_mode="run",
            )

    try:
        result = await run_argv(
            argv=tuple(argv),
            cwd=target.cwd,
            timeout=timeout,
            max_output_bytes=BASH_MAX_OUTPUT_BYTES,
            output_bytes=bytes,
            timeout_behavior=timeout_behavior,
            output_dir=BASH_OUTPUT_DIR,
            max_output_file_bytes=BASH_MAX_OUTPUT_FILE_BYTES,
            env_overrides=env_overrides,
            target=target,
            stdin_path=stdin_path,
            artifacts=artifacts,
            artifact_dir=BASH_ARTIFACT_DIR,
        )
    except BaseException:
        cleanup_artifacts(artifacts, BASH_ARTIFACT_DIR)
        LOGGER.exception(
            "run call failed: invocation_id=%s elapsed_ms=%s",
            invocation_id,
            int((time.monotonic() - started_at) * 1000),
        )
        raise

    LOGGER.info(
        "run call finished: invocation_id=%s code=%s exit_code=%s background_pid=%s duration_ms=%s elapsed_ms=%s",
        invocation_id,
        result.get("code"),
        result.get("exit_code"),
        result.get("background_pid"),
        result.get("duration_ms"),
        int((time.monotonic() - started_at) * 1000),
    )
    return ToolResult(content=result, meta={"summary": summary})


@mcp.tool(annotations={"summary_key": "purpose"})
async def script(
    script: str,
    purpose: PurposeInput = None,
    stdin: Optional[str] = None,
    cwd: Optional[str] = None,
    timeout: Optional[float] = 60,
    bytes: Optional[int] = None,
    timeout_behavior: Literal["background", "kill"] = "background",
    ctx: Context = CurrentContext(),
) -> ToolResult:
    """Execute a Bash script from a managed temporary file.

    ``purpose`` is an optional short user-facing description of why the script
    is run. Provide it for most calls; it does not change or authorize the
    execution. Do not repeat the script or include sensitive payloads.

    The script is syntax-checked with ``bash -n`` before execution. Use
    ``stdin`` for text input to the executed script. Execution on the machine running this server
    uses separate script and stdin files.
    """
    normalized_purpose, purpose_error = _normalize_purpose(purpose)
    if purpose_error is not None:
        return _error_tool_result(
            code="INVALID_PURPOSE",
            message=purpose_error,
            exit_code=-1,
            summary="script",
            tool_mode="script",
        )

    summary = normalized_purpose or "script"
    target = _resolve_execution_target(cwd)
    if not isinstance(script, str) or not script:
        return _error_tool_result(
            code="INVALID_SCRIPT",
            message="script 必须是非空字符串",
            exit_code=-1,
            summary=summary,
            tool_mode="script",
        )
    if "\0" in script:
        return _error_tool_result(
            code="INVALID_SCRIPT",
            message="script 不能包含空字节",
            exit_code=-1,
            summary=summary,
            tool_mode="script",
        )
    script_bytes = script.encode("utf-8")
    if len(script_bytes) > BASH_MAX_SCRIPT_BYTES:
        return _error_tool_result(
            code="SCRIPT_TOO_LARGE",
            message=f"script UTF-8 字节数不能超过 {BASH_MAX_SCRIPT_BYTES}",
            exit_code=-1,
            summary=summary,
            tool_mode="script",
        )
    if stdin is not None and not isinstance(stdin, str):
        return _error_tool_result(
            code="INVALID_STDIN",
            message="stdin 必须是字符串或 null",
            exit_code=-1,
            summary=summary,
            tool_mode="script",
        )
    stdin_bytes = stdin.encode("utf-8") if stdin is not None else None
    if stdin_bytes is not None and len(stdin_bytes) > BASH_MAX_STDIN_BYTES:
        return _error_tool_result(
            code="STDIN_TOO_LARGE",
            message=f"stdin UTF-8 字节数不能超过 {BASH_MAX_STDIN_BYTES}",
            exit_code=-1,
            summary=summary,
            tool_mode="script",
        )

    try:
        env_overrides, caller_session_injected = _build_execution_env(ctx)
    except ValueError as exc:
        return _error_tool_result(
            code="INVALID_META",
            message=str(exc),
            exit_code=-1,
            summary=summary,
            tool_mode="script",
        )

    invocation_id = _extract_invocation_id(ctx) or f"script-{uuid.uuid4().hex[:12]}"
    started_at = time.monotonic()
    LOGGER.info(
        "script call started: invocation_id=%s script_bytes=%s stdin_bytes=%s timeout=%s timeout_behavior=%s cwd=%s",
        invocation_id,
        len(script_bytes),
        len(stdin_bytes) if stdin_bytes is not None else 0,
        timeout,
        timeout_behavior,
        target.cwd,
    )
    _log_execution_env(
        tool_mode="script",
        invocation_id=invocation_id,
        env_overrides=env_overrides,
        caller_session_injected=caller_session_injected,
    )

    try:
        script_path, stdin_path, artifacts = create_script_artifacts(
            script_bytes,
            stdin_bytes,
            BASH_ARTIFACT_DIR,
        )
    except Exception:
        LOGGER.exception(
            "script artifact prepare failed: invocation_id=%s script_bytes=%s stdin_bytes=%s",
            invocation_id,
                len(script_bytes),
            len(stdin_bytes) if stdin_bytes is not None else 0,
        )
        return _error_tool_result(
            code="ARTIFACT_PREPARE_ERROR",
            message="无法准备脚本临时资源",
            exit_code=-1,
            summary=summary,
            tool_mode="script",
        )

    try:
        result = await run_script(
            script_path=script_path,
            cwd=target.cwd,
            timeout=timeout,
            max_output_bytes=BASH_MAX_OUTPUT_BYTES,
            output_bytes=bytes,
            timeout_behavior=timeout_behavior,
            output_dir=BASH_OUTPUT_DIR,
            max_output_file_bytes=BASH_MAX_OUTPUT_FILE_BYTES,
            env_overrides=env_overrides,
            target=target,
            stdin_path=stdin_path,
            artifacts=artifacts,
            artifact_dir=BASH_ARTIFACT_DIR,
        )
    except BaseException:
        cleanup_artifacts(artifacts, BASH_ARTIFACT_DIR)
        LOGGER.exception(
            "script call failed: invocation_id=%s elapsed_ms=%s",
            invocation_id,
            int((time.monotonic() - started_at) * 1000),
        )
        raise

    LOGGER.info(
        "script call finished: invocation_id=%s code=%s exit_code=%s background_pid=%s duration_ms=%s elapsed_ms=%s",
        invocation_id,
        result.get("code"),
        result.get("exit_code"),
        result.get("background_pid"),
        result.get("duration_ms"),
        int((time.monotonic() - started_at) * 1000),
    )
    return ToolResult(content=result, meta={"summary": summary})


@mcp.tool()
async def get(
    pid: int,
    timeout: Optional[float] = 0,
    bytes: int = 200,
) -> ToolResult:
    """Get the status and bounded output preview for a tracked background process.

    Use this after an execution tool returns a ``BACKGROUND`` pid to check
    whether the process is still running and to read its output head and tail. A
    positive ``timeout`` waits for completion for at most that many seconds.
    The PID belongs to a task tracked by this server instance.
    """
    if bytes < 0:
        return _error_tool_result(
            code="INVALID_BYTES",
            message="bytes 不能小于 0",
            exit_code=None,
            summary=_build_get_summary(pid),
        )
    if timeout is not None and timeout < 0:
        return _error_tool_result(
            code="INVALID_TIMEOUT",
            message="timeout 不能小于 0",
            exit_code=None,
            summary=_build_get_summary(pid),
        )

    result = await get_background_result(
        pid=pid,
        timeout=timeout,
        output_bytes=min(bytes, BASH_MAX_OUTPUT_BYTES),
    )
    return ToolResult(content=result, meta={"summary": _build_get_summary(pid)})


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    """Start the FastMCP server."""
    print("启动 Bash MCP 服务器...")
    if SERVER_LOG_ENABLED and SERVER_LOG_FILE:
        print(f"服务日志文件: {SERVER_LOG_FILE}")
    else:
        print("服务日志文件: 已禁用")

    shell_path, _ = get_shell_config()
    working_dir = DEFAULT_CWD or os.getcwd()
    try:
        removed_artifacts = cleanup_expired_artifacts(
            BASH_ARTIFACT_DIR,
            BASH_ARTIFACT_TTL_SECONDS,
        )
    except Exception:
        removed_artifacts = 0
        LOGGER.exception("artifact startup cleanup failed")

    try:
        removed_outputs = cleanup_expired_outputs(
            BASH_OUTPUT_DIR,
            BASH_OUTPUT_TTL_SECONDS,
        )
    except Exception:
        removed_outputs = 0
        LOGGER.exception("output startup cleanup failed")

    LOGGER.info(
        "startup cleanup: removed_artifacts=%s removed_outputs=%s",
        removed_artifacts,
        removed_outputs,
    )

    LOGGER.info(
        "server starting: transport=%s host=%s port=%s cwd=%s shell=%s",
        TRANSPORT,
        SSE_HOST,
        SSE_PORT,
        working_dir,
        shell_path,
    )

    print(f"传输模式: {TRANSPORT}")
    print(f"默认工作目录: {working_dir}")
    print(f"Shell: {shell_path}")
    print(f"输出截断: {BASH_MAX_OUTPUT_BYTES} bytes")
    print(f"输出文件上限: {BASH_MAX_OUTPUT_FILE_BYTES} bytes")
    print(f"命令输出目录: {BASH_OUTPUT_DIR}")
    print(f"临时资源目录: {BASH_ARTIFACT_DIR}")
    if removed_artifacts:
        print(f"已清理过期临时资源: {removed_artifacts}")
    if removed_outputs:
        print(f"已清理过期输出文件: {removed_outputs}")

    try:
        if TRANSPORT == "sse":
            print(f"SSE 地址: http://{SSE_HOST}:{SSE_PORT}/sse")
            LOGGER.info("starting in sse transport")
            mcp.run(transport="sse", host=SSE_HOST, port=SSE_PORT)
        elif TRANSPORT == "http":
            print(f"HTTP 地址: http://{SSE_HOST}:{SSE_PORT}")
            LOGGER.info("starting in http transport")
            mcp.run(transport="http", host=SSE_HOST, port=SSE_PORT)
        else:
            LOGGER.info("starting in stdio transport")
            mcp.run()
    except Exception as e:
        print(f"启动服务器失败: {e}")
        LOGGER.exception("server startup failed: %s", e)
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
