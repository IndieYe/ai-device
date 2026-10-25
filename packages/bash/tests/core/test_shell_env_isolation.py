"""Service configuration must not reach user commands through the environment.

Hosts supply MCP settings through ``config.json`` ``env``/``inherit_env``, which
land in this process's environment. Commands inherit that mapping, so anything
excluded here is what keeps service settings and caller secrets out of command
output and ``env`` dumps.
"""

import os
import sys

import pytest

import mcp_server_bash.backends.local as local
from mcp_server_bash.core import ExecutionRuntime
from mcp_server_bash.env_keys import SERVICE_CONFIG_ENV_KEYS


@pytest.fixture
def clean_runtime(tmp_path):
    def _make():
        return ExecutionRuntime(
            output_dir=str(tmp_path / "outputs"), artifact_dir=str(tmp_path / "artifacts")
        )

    return _make


def test_shell_env_excludes_every_service_config_key(monkeypatch):
    for key in SERVICE_CONFIG_ENV_KEYS:
        monkeypatch.setenv(key, "service-value")

    env = local.get_shell_env()

    assert set(env).isdisjoint(SERVICE_CONFIG_ENV_KEYS)


def test_shell_env_keeps_unrelated_and_required_variables(monkeypatch):
    monkeypatch.setenv("UNRELATED_APP_VAR", "keep-me")
    monkeypatch.setenv("BASH_OUTPUT_DIR", "/service/only")

    env = local.get_shell_env()

    assert env["UNRELATED_APP_VAR"] == "keep-me"
    assert env["PATH"] == os.environ["PATH"]
    assert "BASH_OUTPUT_DIR" not in env


@pytest.mark.asyncio
async def test_commands_do_not_see_caller_context_token(monkeypatch, clean_runtime):
    monkeypatch.setenv("BASH_CALLER_CONTEXT_TOKEN", "secret-token")
    runtime = clean_runtime()
    try:
        result = await runtime.run_argv(
            (sys.executable, "-c", "import os; print(os.environ.get('BASH_CALLER_CONTEXT_TOKEN'))"),
            cwd=str(runtime.output_dir),
        )
    finally:
        await runtime.aclose()

    assert result["code"] == "OK"
    assert result["output"].strip() == "None"


@pytest.mark.asyncio
async def test_explicit_shell_env_override_still_reaches_the_command(monkeypatch, clean_runtime):
    """``meta.shell_env`` stays available for callers that deliberately pass a value."""
    monkeypatch.setenv("BASH_CALLER_CONTEXT_TOKEN", "secret-token")
    runtime = clean_runtime()
    try:
        result = await runtime.run_argv(
            (sys.executable, "-c", "import os; print(os.environ['DELIBERATE'])"),
            cwd=str(runtime.output_dir),
            env_overrides={"DELIBERATE": "explicit"},
        )
    finally:
        await runtime.aclose()

    assert result["code"] == "OK"
    assert result["output"].strip() == "explicit"
