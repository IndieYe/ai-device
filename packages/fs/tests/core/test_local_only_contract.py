"""Local-only signatures and rejection through a real in-memory MCP session."""

import inspect
import json
from unittest.mock import patch

import anyio
from fastmcp import Client
import pytest

from mcp_server_fs.server import create_mcp


TOOLS = {
    "read_file", "read_image", "write_file", "edit_file", "insert_file", "apply_patch"
}


def _invoke(mcp, name, arguments):
    async def call():
        async with Client(mcp) as client:
            return await client.call_tool(name, arguments, raise_on_error=False)

    return anyio.run(call)


def test_local_only_signatures_and_registered_schemas():
    mcp, functions = create_mcp()

    async def discover():
        async with Client(mcp) as client:
            return await client.list_tools()

    tools = anyio.run(discover)
    assert {tool.name for tool in tools} == TOOLS
    for tool in tools:
        assert "host" not in tool.inputSchema["properties"]
        assert "host" not in inspect.signature(functions[tool.name]).parameters


@pytest.mark.parametrize("name", sorted(TOOLS))
@pytest.mark.parametrize("host", ["prod-1", "", None])
def test_removed_host_is_rejected_before_file_operation(tmp_path, name, host):
    target = tmp_path / "file.txt"
    target.write_text("original", encoding="utf-8")
    arguments = {
        "read_file": {"path": str(target)},
        "read_image": {"path": str(target)},
        "write_file": {"path": str(target), "content": "changed"},
        "edit_file": {"path": str(target), "edits": [{"oldText": "original", "newText": "changed"}]},
        "insert_file": {"path": str(target), "text": "changed", "at_end": True},
        "apply_patch": {"patch": f"*** Begin Patch\n*** Delete File: {target}\n*** End Patch"},
    }[name]
    mcp, _ = create_mcp()
    # All read/write entry paths use these helpers. Reaching them is a test failure.
    helper = "execute_patch" if name == "apply_patch" else "validate_path"
    with patch(f"mcp_server_fs.core.{name}.{helper}", side_effect=AssertionError("operation started")) as operation:
        result = _invoke(mcp, name, {**arguments, "host": host})

    assert result.is_error is True
    assert "host" in result.content[0].text
    operation.assert_not_called()
    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [target]


def test_registered_local_write_edit_read_and_patch_still_work(tmp_path):
    target = tmp_path / "local.txt"
    mcp, _ = create_mcp()
    write = _invoke(mcp, "write_file", {"path": str(target), "content": "hello"})
    assert write.is_error is False
    assert json.loads(write.content[0].text)["success"] is True
    edit = _invoke(mcp, "edit_file", {"path": str(target), "edits": [{"oldText": "hello", "newText": "world"}]})
    assert edit.is_error is False
    read = _invoke(mcp, "read_file", {"path": str(target)})
    assert read.is_error is False
    assert json.loads(read.content[0].text)["content"] == "world"
    delete = _invoke(mcp, "apply_patch", {"patch": f"*** Begin Patch\n*** Delete File: {target}\n*** End Patch"})
    assert delete.is_error is False
    assert not target.exists()
