"""Real stdio -> HTTP -> WebSocket -> device Bash task acceptance."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from conftest import running_client

from device_runtime import __version__
from device_runtime.executor import LocalExecutor


async def test_real_task_stdio_snapshots_disconnect_restart(live_server, tmp_path, monkeypatch):
    mcp = pytest.importorskip("mcp")
    stdio_client = pytest.importorskip("mcp.client.stdio").stdio_client
    url, _, _ = live_server

    state_dir = tmp_path / "client"
    gate, marker = tmp_path / "gate", tmp_path / "marker"
    executor = LocalExecutor(state_dir, "laptop-one")
    params = mcp.StdioServerParameters(
        command=sys.executable,
        args=["-m", "device_runtime.mcp_server"],
        env={
            "DEVICE_RUNTIME_API_URL": url.replace("ws://", "http://"),
            "DEVICE_RUNTIME_ADMIN_TOKEN": "test-only-admin-token-not-a-secret",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    async with stdio_client(params) as (read, write), mcp.ClientSession(read, write) as session:
        identity = await session.initialize()
        assert identity.serverInfo.version == __version__
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert len(tools) == 12
        for name in ("run", "bash", "script", "get"):
            assert "pid" not in tools[name].inputSchema["properties"]
        args = {
            "cwd": str(tmp_path),
            "stdin": "中文\n",
            "argv": [
                sys.executable,
                "-c",
                "import pathlib,sys,time; pathlib.Path(sys.argv[1]).open('a').write('once\\n'); print(sys.stdin.read(),end='',flush=True); gate=pathlib.Path(sys.argv[2]);\nwhile not gate.exists(): time.sleep(.01)\nprint('done',flush=True)",
                str(marker),
                str(gate),
            ],
        }
        async with running_client(live_server, state_dir, executor):
            response = await session.call_tool(
                "run",
                {
                    "device_id": "laptop-one",
                    "operation_id": "stdio-task",
                    "timeout": 0.01,
                    **args,
                },
            )
            assert not response.isError
            started = json.loads(response.content[0].text)
            assert started["result"]["data"]["state"] == "running"
            output_path = started["result"]["data"]["output_path"]
        assert not executor.tasks._owned
        recovered = LocalExecutor(state_dir, "laptop-one")
        async with running_client(live_server, state_dir, recovered):
            response = await session.call_tool(
                "get",
                {
                    "device_id": "laptop-one",
                    "execution_id": "stdio-task",
                    "timeout": 2,
                    "bytes": 1000,
                },
            )
            result = json.loads(response.content[0].text)["result"]["data"]
            assert result["state"] == "unknown"
            assert result["output_path"] == output_path
            assert marker.read_text() == "once\n"
            assert not gate.exists()


async def test_slow_task_output_keeps_http_and_other_device_operation_responsive(
    live_server, tmp_path, monkeypatch
):
    import threading

    from mcp_server_bash import output_capture as capture

    _, _, http = live_server
    ready, release = threading.Event(), threading.Event()
    original = capture._persist_output

    def slow(path, chunk, *, truncate=False):
        if chunk:
            ready.set()
            assert release.wait(2)
        return original(path, chunk, truncate=truncate)

    monkeypatch.setattr(capture, "_persist_output", slow)
    executor = LocalExecutor(tmp_path / "client", "laptop-one")
    file = tmp_path / "independent"
    file.write_text("responsive")
    async with running_client(live_server, tmp_path / "client", executor):
        try:
            response = await http.post(
                "/v1/operations",
                json={
                    "device_id": "laptop-one",
                    "operation_id": "slow-output",
                    "action": "bash.execute",
                    "arguments": {
                        "mode": "run",
                        "timeout": 0.001,
                        "cwd": str(tmp_path),
                        "argv": [
                            sys.executable,
                            "-c",
                            "import time; print('slow',flush=True); time.sleep(10)",
                        ],
                    },
                },
            )
            assert response.status_code == 200
            async with asyncio.timeout(1):
                while not ready.is_set():
                    await asyncio.sleep(0.005)
                assert (await http.get("/v1/devices")).status_code == 200
                response = await http.post(
                    "/v1/operations",
                    json={
                        "device_id": "laptop-one",
                        "operation_id": "independent-read",
                        "action": "fs.read_file",
                        "arguments": {"path": str(file)},
                    },
                )
                assert response.status_code == 200
                while True:
                    record = (
                        await http.get(
                            "/v1/operations/independent-read", params={"device_id": "laptop-one"}
                        )
                    ).json()
                    if record["state"] == "succeeded":
                        break
                    await asyncio.sleep(0.01)
                assert record["result"]["data"]["content"] == "responsive"
        finally:
            release.set()


@pytest.mark.parametrize("delayed", [False, True])
async def test_real_bash_stdio_bounded_wait(live_server, tmp_path, delayed):
    """Real SDK stdio -> HTTP -> WebSocket -> Bash executor, without mocks."""
    mcp = pytest.importorskip("mcp")
    stdio_client = pytest.importorskip("mcp.client.stdio").stdio_client
    url, _, _ = live_server
    state_dir = tmp_path / "client"
    marker, gate = tmp_path / "marker", tmp_path / "gate"
    executor = LocalExecutor(state_dir, "laptop-one")
    if not delayed:
        gate.touch()
    params = mcp.StdioServerParameters(
        command=sys.executable,
        args=["-m", "device_runtime.mcp_server"],
        env={
            "DEVICE_RUNTIME_API_URL": url.replace("ws://", "http://"),
            "DEVICE_RUNTIME_ADMIN_TOKEN": "test-only-admin-token-not-a-secret",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    arguments = {
        "device_id": "laptop-one",
        "operation_id": "bash-wait-e2e",
        "cwd": str(tmp_path),
        "timeout": 0.1 if delayed else 5,
        "argv": [
            sys.executable,
            "-c",
            (
                "import pathlib,sys,time; pathlib.Path(sys.argv[1]).open('a').write('once\\n'); "
                "gate=pathlib.Path(sys.argv[2]);\nwhile not gate.exists(): time.sleep(.01)\n"
                "print('bash-wait-output',flush=True)"
            ),
            str(marker),
            str(gate),
        ],
    }
    async with (
        running_client(live_server, state_dir, executor),
        stdio_client(params) as (read, write),
        mcp.ClientSession(read, write) as session,
    ):
        await session.initialize()
        async with asyncio.timeout(8):
            response = await session.call_tool("run", arguments)
            assert not response.isError, response.content
            value = json.loads(response.content[0].text)
            if delayed:
                assert value["result"]["data"]["state"] == "running"
                gate.touch()
                response = await session.call_tool(
                    "get",
                    {
                        "device_id": "laptop-one",
                        "execution_id": "bash-wait-e2e",
                        "timeout": 3,
                        "bytes": 1000,
                    },
                )
                value = json.loads(response.content[0].text)
            assert value["state"] == "succeeded"
            assert value["result"]["data"]["execution_id"] == "bash-wait-e2e"
            assert value["device_id"] == "laptop-one"
            assert value["result"]["location"] == {
                "namespace": "device",
                "device_id": "laptop-one",
            }
            data = value["result"]["data"]
            assert data["success"] and data["exit_code"] == 0
            assert data["output"] == "bash-wait-output\n"
            assert Path(data["output_path"]).is_relative_to(state_dir)
            repeated = await session.call_tool("run", arguments)
            assert not repeated.isError
            assert json.loads(repeated.content[0].text)["operation_id"] == "bash-wait-e2e"
            assert marker.read_text() == "once\n"
