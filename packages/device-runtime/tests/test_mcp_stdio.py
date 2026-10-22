"""Real stdio SDK -> isolated loopback; no device command execution."""

import json
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from device_runtime import __version__

mcp = pytest.importorskip("mcp")
ClientSession = mcp.ClientSession
StdioServerParameters = mcp.StdioServerParameters
stdio_client = pytest.importorskip("mcp.client.stdio").stdio_client


@contextmanager
def center():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(value)
            data = json.dumps(
                {
                    "device_id": value["device_id"],
                    "operation_id": value["operation_id"],
                    "state": "unknown",
                    "result": None,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def test_advertised_tool_names_use_server_namespace():
    from device_runtime.mcp_server import create_mcp

    tools = await create_mcp("http://127.0.0.1:1", "isolated-schema-token").list_tools()
    assert {tool.name for tool in tools} == {
        "get_version",
        "list_devices",
        "get_operation",
        "run",
        "bash",
        "script",
        "get",
        "read_file",
        "write_file",
        "edit_file",
        "insert_file",
        "read_image",
    }
    assert len(tools) == 12


async def test_stdio_run_integer_contract(tmp_path):
    with center() as (port, calls):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "device_runtime.mcp_server"],
            env={
                "DEVICE_RUNTIME_API_URL": f"http://127.0.0.1:{port}/device-runtime",
                "DEVICE_RUNTIME_ADMIN_TOKEN": "isolated-stdio-token",
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            },
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            identity = await session.initialize()
            assert identity.serverInfo.version == __version__
            base = {
                "device_id": "test-device",
                "argv": ["/bin/echo", "never-run"],
                "cwd": str(tmp_path),
            }
            for index, arguments in enumerate(
                (
                    {},
                    {"timeout": 60},
                    {"timeout": 1, "bytes": 1},
                    {"timeout": 300, "bytes": 131072},
                )
            ):
                before = len(calls)
                result = await session.call_tool(
                    "run", {**base, "operation_id": f"valid-{index}", **arguments}
                )
                assert not result.isError, result.content
                assert len(calls) == before + 1
                args = calls[-1]["arguments"]
                assert args["timeout"] == arguments.get("timeout", 60)
                assert isinstance(args["timeout"], (int, float))
                assert args["bytes"] == arguments.get("bytes", 8192)
            for index, arguments in enumerate(
                (
                    {"timeout": 0},
                    {"timeout": 301},
                    {"timeout": "60"},
                    {"bytes": 131073},
                    {"bytes": 1.5},
                    {"bytes": "1"},
                    {"bytes": True},
                )
            ):
                before = len(calls)
                result = await session.call_tool(
                    "run", {**base, "operation_id": f"invalid-{index}", **arguments}
                )
                assert result.isError
                assert len(calls) == before


@pytest.mark.parametrize("key,maximum,default", [("timeout", 300, 60), ("bytes", 131072, 8192)])
async def test_run_advertised_schema_matches_protocol(key, maximum, default):
    from device_runtime.mcp_server import create_mcp

    tools = await create_mcp("http://127.0.0.1:1", "isolated-schema-token").list_tools()
    schema = next(t.inputSchema for t in tools if t.name == "run")
    field = schema["properties"][key]
    assert field["type"] == ("number" if key == "timeout" else "integer")
    assert field.get("minimum", field.get("exclusiveMinimum")) == 0
    assert field["maximum"] == maximum
    assert field["default"] == default


async def test_get_schema_has_execution_id_without_query_id():
    from device_runtime.mcp_server import create_mcp

    tools = await create_mcp("http://127.0.0.1:1", "isolated-schema-token").list_tools()
    schema = next(tool for tool in tools if tool.name == "get").inputSchema
    assert set(schema["required"]) == {"device_id", "execution_id"}
    assert schema["properties"]["timeout"]["default"] == 0
