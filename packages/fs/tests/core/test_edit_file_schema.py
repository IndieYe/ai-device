"""MCP schema and validation tests for edit_file."""

from __future__ import annotations

import json

import anyio
import pytest
from fastmcp import Client

from mcp_server_fs.server import create_mcp


def _call_edit_file(arguments: dict):
    mcp, _ = create_mcp()

    async def call_tool():
        async with Client(mcp) as client:
            return await client.call_tool(
                "edit_file",
                arguments,
                raise_on_error=False,
            )

    return anyio.run(call_tool)


def test_registered_edit_file_schema_requires_strict_edit_fields():
    mcp, _ = create_mcp()

    async def list_registered_tools():
        async with Client(mcp) as client:
            return await client.list_tools()

    tools = anyio.run(list_registered_tools)
    edit_file_tool = next(tool for tool in tools if tool.name == "edit_file")
    edit_item_schema = edit_file_tool.inputSchema["properties"]["edits"]["items"]

    assert edit_item_schema["type"] == "object"
    assert edit_item_schema["additionalProperties"] is False
    assert edit_item_schema["required"] == ["oldText", "newText"]
    assert edit_item_schema["properties"] == {
        "oldText": {"type": "string"},
        "newText": {"type": "string"},
    }


def test_edit_file_accepts_valid_structured_edit(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("hello", encoding="utf-8")

    result = _call_edit_file(
        {
            "path": str(target),
            "edits": [{"oldText": "hello", "newText": "goodbye"}],
        }
    )

    assert result.is_error is False
    assert json.loads(result.content[0].text)["success"] is True
    assert target.read_text(encoding="utf-8") == "goodbye"


@pytest.mark.parametrize(
    ("edit", "expected_error_field"),
    [
        ({"old_text": "hello", "newText": "goodbye"}, "oldText"),
        ({"oldText": "hello"}, "newText"),
        ({"oldText": "hello", "newText": "goodbye", "extra": "value"}, "extra"),
    ],
)
def test_edit_file_rejects_invalid_edit_shape_before_execution(
    tmp_path,
    edit,
    expected_error_field,
):
    target = tmp_path / "sample.txt"
    target.write_text("hello", encoding="utf-8")

    result = _call_edit_file({"path": str(target), "edits": [edit]})

    assert result.is_error is True
    assert expected_error_field in result.content[0].text
    assert target.read_text(encoding="utf-8") == "hello"
