"""MCP schema and validation tests for read_file."""

from __future__ import annotations

import json

import anyio
import pytest
from fastmcp import Client

from mcp_server_fs.defaults import DEFAULT_MAX_CHARS, HARD_MAX_CHARS
from mcp_server_fs.server import create_mcp


def _call_read_file(arguments: dict):
    mcp, _ = create_mcp()

    async def call_tool():
        async with Client(mcp) as client:
            return await client.call_tool(
                "read_file",
                arguments,
                raise_on_error=False,
            )

    return anyio.run(call_tool)


def test_registered_read_file_schema_declares_max_chars_bounds():
    mcp, _ = create_mcp()

    async def list_registered_tools():
        async with Client(mcp) as client:
            return await client.list_tools()

    tools = anyio.run(list_registered_tools)
    read_file_tool = next(tool for tool in tools if tool.name == "read_file")
    max_chars_schema = read_file_tool.inputSchema["properties"]["max_chars"]

    assert max_chars_schema["type"] == "integer"
    assert max_chars_schema["default"] == DEFAULT_MAX_CHARS
    assert max_chars_schema["minimum"] == 1
    assert max_chars_schema["maximum"] == HARD_MAX_CHARS


@pytest.mark.parametrize("max_chars", [1, DEFAULT_MAX_CHARS, HARD_MAX_CHARS])
def test_read_file_accepts_valid_max_chars_bounds(tmp_path, max_chars):
    target = tmp_path / "sample.txt"
    target.write_text("x" * (HARD_MAX_CHARS + 5), encoding="utf-8")

    result = _call_read_file({"path": str(target), "max_chars": max_chars})

    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["truncated"] is True
    assert payload["returned_chars"] == max_chars
    assert len(payload["content"]) == max_chars


@pytest.mark.parametrize(
    "max_chars",
    [0, -1, True, 1.5, "100", HARD_MAX_CHARS + 1],
)
def test_read_file_rejects_invalid_max_chars_before_execution(
    tmp_path,
    monkeypatch,
    max_chars,
):
    target = tmp_path / "sample.txt"
    target.write_text("hello", encoding="utf-8")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("core.read_file should not be called for invalid max_chars")

    monkeypatch.setattr("mcp_server_fs.core.read_file", fail_if_called)

    result = _call_read_file({"path": str(target), "max_chars": max_chars})

    assert result.is_error is True
    assert "max_chars" in result.content[0].text
