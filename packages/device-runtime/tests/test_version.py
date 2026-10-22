"""Runtime identity, mixed-version connections and read-only public queries."""

import asyncio
import json
import os
import subprocess
import sys

import httpx
import pytest
from conftest import ADMIN, TOKEN, FakeExecutor, eventually
from websockets.asyncio.client import connect

from device_runtime import __version__
from device_runtime.client import DeviceClient
from device_runtime.http_client import DeviceAPI, DeviceAPIError
from device_runtime.version import (
    VERSION_HEADER,
    parse_version_header,
    runtime_version,
    version_header,
)


def test_identity_and_header_are_bounded_snapshots(monkeypatch):
    from device_runtime import version

    identity = runtime_version()
    assert identity["version"] == __version__ == "0.2.2"
    assert identity["build_id"].startswith("sha256:")
    assert len(identity["build_id"]) == 71
    assert parse_version_header(version_header()).model_dump() == identity
    monkeypatch.setattr(version, "_source_fingerprint", lambda: "changed-on-disk")
    assert runtime_version() == identity
    identity["version"] = "caller-mutated"
    assert runtime_version()["version"] == __version__


@pytest.mark.parametrize(
    "value",
    [
        None,
        "not-json",
        "x" * 513,
        '{"version":123}',
        '{"version":"1.0","extra":true}',
        '{"version":"1.0","build_id":"bad"}',
    ],
)
def test_invalid_optional_identity_is_unknown(value):
    assert parse_version_header(value) is None


async def test_client_reports_identity_and_disconnect_does_not_claim_running(live_server, tmp_path):
    url, service, http = live_server
    client = DeviceClient(
        server_url=url,
        device_id="laptop-one",
        token=TOKEN,
        state_dir=tmp_path / "client",
        executor=FakeExecutor(),
    )
    task = asyncio.create_task(client.run())
    try:

        async def online():
            return "laptop-one" in service.peers

        await eventually(online)
        device = (await http.get("/v1/devices")).json()[0]
        assert device["runtime_version"] == runtime_version()
        async with DeviceAPI(url.replace("ws:", "http:"), ADMIN) as api:
            assert (await api.list_devices())["devices"][0]["runtime_version"] == runtime_version()
            assert await api.get_version() == {"role": "center", **runtime_version()}
        response = await http.get("/v1/version", headers={"Authorization": "Bearer invalid"})
        assert response.status_code == 401
    finally:
        await client.close()
        await task

    async def offline():
        return "laptop-one" not in service.peers

    await eventually(offline)
    assert (await http.get("/v1/devices")).json()[0]["runtime_version"] is None


@pytest.mark.parametrize("header", [None, "invalid", "x" * 513])
async def test_old_or_malformed_client_connects_without_identity(live_server, header):
    url, _, http = live_server
    headers = {"Authorization": "Bearer " + TOKEN}
    if header is not None:
        headers[VERSION_HEADER] = header
    async with connect(
        url + "/v1/devices/laptop-one/connect", additional_headers=headers
    ) as socket:
        await socket.send(
            json.dumps({"v": 1, "type": "hello", "capabilities": [], "platform": "linux"})
        )
        assert json.loads(await socket.recv()) == {
            "v": 1,
            "type": "welcome",
            "device_id": "laptop-one",
        }
        device = (await http.get("/v1/devices")).json()[0]
        assert device["online"] is True
        assert device["runtime_version"] is None


async def test_new_client_preserves_exact_legacy_hello(tmp_path):
    from websockets.asyncio.server import serve

    received = asyncio.Future()

    async def legacy(socket):
        hello = json.loads(await socket.recv())
        received.set_result(hello)
        assert hello == {
            "v": 1,
            "type": "hello",
            "capabilities": list(FakeExecutor.capabilities),
            "platform": sys.platform,
        }
        await socket.send(json.dumps({"v": 1, "type": "welcome", "device_id": "laptop-one"}))
        await socket.wait_closed()

    async with serve(legacy, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = DeviceClient(
            server_url=f"ws://127.0.0.1:{port}",
            device_id="laptop-one",
            token=TOKEN,
            state_dir=tmp_path / "client",
            executor=FakeExecutor(),
        )
        task = asyncio.create_task(client.run())
        try:
            async with asyncio.timeout(5):
                await received
        finally:
            await client.close()
            await task


async def test_center_query_is_read_only_and_validated():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"role": "center", **runtime_version()})

    async with DeviceAPI(
        "http://127.0.0.1:1", ADMIN, transport=httpx.MockTransport(handler)
    ) as api:
        assert (await api.get_version())["version"] == __version__
    assert calls == [("GET", "/v1/version")]
    async with DeviceAPI(
        "http://127.0.0.1:1",
        ADMIN,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"role": "center", "version": 123})
        ),
    ) as api:
        with pytest.raises(DeviceAPIError, match="center_version_invalid"):
            await api.get_version()


async def test_mcp_exposes_local_and_center_identity(monkeypatch):
    from device_runtime.mcp_server import create_mcp

    async def query(_):
        return {"role": "center", **runtime_version()}

    monkeypatch.setattr(DeviceAPI, "get_version", query)
    server = create_mcp("http://127.0.0.1:1", ADMIN)
    assert server._mcp_server.version == __version__
    content = await server.call_tool("get_version", {})
    structured = json.loads(content[0].text)
    assert structured == {
        "mcp": runtime_version(),
        "center": {"role": "center", **runtime_version()},
    }


@pytest.mark.parametrize("module", ["device_runtime.cli", "device_runtime.mcp_server"])
def test_local_version_needs_no_credentials(module):
    env = {k: v for k, v in os.environ.items() if not k.startswith("DEVICE_RUNTIME_")}
    result = subprocess.run(
        [sys.executable, "-m", module, "--version"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == runtime_version()


async def test_ops_version_queries_center(monkeypatch, capsys):
    from device_runtime.operations_cli import main

    async def query(_):
        return {"role": "center", **runtime_version()}

    monkeypatch.setattr(DeviceAPI, "get_version", query)
    monkeypatch.setenv("DEVICE_RUNTIME_ADMIN_TOKEN", ADMIN)
    # CLI owns its event loop; invoke in a worker, not from this test's loop.
    assert await asyncio.to_thread(main, ["--server-url", "http://127.0.0.1:1", "version"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["ok"] is True
    assert value["data"]["version"] == __version__
