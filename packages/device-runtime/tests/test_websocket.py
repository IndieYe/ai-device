import asyncio
import sys

import pytest
from conftest import TOKEN, FakeExecutor, eventually, running_client
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from device_runtime.executor import LocalExecutor
from device_runtime.journal import Journal
from device_runtime.protocol import Operation


def payload(operation_id="op-net", action="fs.write_file", arguments=None):
    return {
        "operation_id": operation_id,
        "device_id": "laptop-one",
        "action": action,
        "arguments": arguments or {"path": "/tmp/device-only-test", "content": "x"},
    }


async def terminal(http, operation_id):
    record = None

    async def check():
        nonlocal record
        response = await http.get("/v1/operations/" + operation_id)
        record = response.json()
        return response.status_code == 200 and record["state"] in ("succeeded", "failed")

    await eventually(check)
    return record


async def test_live_success_idempotent_and_offline(live_server, tmp_path):
    _, _, http = live_server
    assert (await http.post("/v1/operations", json=payload())).status_code == 409
    executor = FakeExecutor()
    async with running_client(live_server, tmp_path / "client", executor):
        assert (await http.post("/v1/operations", json=payload())).status_code == 200
        result = await terminal(http, "op-net")
        assert result["result"]["location"] == {"namespace": "device", "device_id": "laptop-one"}
        assert (await http.post("/v1/operations", json=payload())).json()["state"] == "succeeded"
        assert executor.calls == 1
        changed = payload(arguments={"path": "/tmp/different", "content": "y"})
        assert (await http.post("/v1/operations", json=changed)).status_code == 409


async def test_disconnect_during_execution_recovers_without_resend(live_server, tmp_path):
    _, service, http = live_server
    executor = FakeExecutor(paused=True)
    async with running_client(live_server, tmp_path / "client", executor):
        await http.post("/v1/operations", json=payload())
        await executor.started.wait()
        old_peer = service.peers["laptop-one"]
        await old_peer.websocket.close(code=1012)

        async def reconnected():
            return service.peers.get("laptop-one") not in (None, old_peer)

        await eventually(reconnected)
        assert executor.calls == 1
        assert (await http.post("/v1/operations", json=payload())).status_code == 200
        executor.release.set()
        assert (await terminal(http, "op-net"))["state"] == "succeeded"
        assert executor.calls == 1


async def test_lost_result_ack_replays_result_not_command(live_server, tmp_path, monkeypatch):
    _, service, http = live_server
    original = service.report
    lost = asyncio.Event()

    # Raise the route's expected transport-disconnect event, not a persistence failure.
    from starlette.websockets import WebSocketDisconnect

    async def dropped(device_id, peer, report):
        if report.state == "succeeded" and not lost.is_set():
            lost.set()
            await peer.websocket.close(code=1012)
            raise WebSocketDisconnect(1012)
        await original(device_id, peer, report)

    monkeypatch.setattr(service, "report", dropped)
    executor = FakeExecutor()
    async with running_client(live_server, tmp_path / "client", executor) as client:
        await http.post("/v1/operations", json=payload())
        await asyncio.wait_for(lost.wait(), 5)
        assert (await terminal(http, "op-net"))["state"] == "succeeded"

        async def confirmed():
            return (await client.journal.get("op-net"))["confirmed"]

        await eventually(confirmed)
        assert executor.calls == 1


async def test_client_stop_restart_is_unknown_and_never_reexecutes(live_server, tmp_path):
    _, service, http = live_server
    state = tmp_path / "client"
    first = FakeExecutor(paused=True)
    async with running_client(live_server, state, first):
        await http.post("/v1/operations", json=payload())
        await first.started.wait()

    async def offline():
        return "laptop-one" not in service.peers

    await eventually(offline)
    assert (await http.get("/v1/operations/op-net")).json()["state"] == "unknown"
    second = FakeExecutor()
    async with running_client(live_server, state, second):
        assert (await http.post("/v1/operations", json=payload())).json()["state"] == "unknown"
        await asyncio.sleep(0.05)
        assert second.calls == 0


async def test_auth_missing_target_and_duplicate_connection(live_server, tmp_path):
    url, service, http = live_server
    assert (
        await http.get("/v1/devices", headers={"Authorization": "Bearer wrong"})
    ).status_code == 401
    with pytest.raises(InvalidStatus):
        async with connect(
            url + "/v1/devices/laptop-one/connect",
            additional_headers={"Authorization": "Bearer wrong"},
            proxy=None,
        ):
            pass
    body = payload()
    del body["device_id"]
    assert (await http.post("/v1/operations", json=body)).status_code == 422
    async with running_client(live_server, tmp_path / "client", FakeExecutor()):
        first_peer = service.peers["laptop-one"]
        async with connect(
            url + "/v1/devices/laptop-one/connect",
            additional_headers={"Authorization": "Bearer " + TOKEN},
            proxy=None,
        ) as ws:
            await ws.send('{"v":1,"type":"hello","capabilities":[],"platform":"linux"}')
            with pytest.raises(ConnectionClosed):
                await ws.recv()
        assert service.peers["laptop-one"] is first_peer


async def test_multi_page_outbox_reconciles(live_server, tmp_path):
    url, service, http = live_server
    state = tmp_path / "client"
    journal = Journal(state / "operations.sqlite3", owner=url + "/v1/devices/laptop-one/connect")
    await journal.initialize()
    for index in range(120):
        op = Operation.model_validate(payload(f"op-{index:03d}"))
        await service.journal.accept(op, "sent")
        await journal.accept(op, "running")
        await journal.update(op.operation_id, "succeeded", {"data": {"success": True}})
    await journal.close()
    executor = FakeExecutor()
    async with running_client(live_server, state, executor) as client:
        await terminal(http, "op-119")

        async def all_confirmed():
            return not await client.journal.unconfirmed()

        await eventually(all_confirmed)
        assert executor.calls == 0


async def test_actual_core_argv_and_file_roundtrip(live_server, tmp_path):
    _, _, http = live_server
    state = tmp_path / "client"
    executor = LocalExecutor(state, "laptop-one")
    file = tmp_path / "device file with spaces.txt"
    async with running_client(live_server, state, executor):

        async def call(op_id, action, args):
            response = await http.post("/v1/operations", json=payload(op_id, action, args))
            assert response.status_code == 200, response.text
            result = await terminal(http, op_id)
            assert result["result"]["location"]["device_id"] == "laptop-one"
            return result

        assert (
            await call("file-write", "fs.write_file", {"path": str(file), "content": "alpha\n"})
        )["state"] == "succeeded"
        result = await call(
            "argv-read",
            "bash.execute",
            {
                "mode": "run",
                "argv": [
                    sys.executable,
                    "-c",
                    'import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text(),end="")',
                    str(file),
                ],
                "cwd": str(tmp_path),
                "timeout": 5,
            },
        )
        assert result["state"] == "succeeded"
        assert result["result"]["data"]["output"] == "alpha\n"
        assert str(state / "output") in result["result"]["data"]["output_path"]
        assert (
            await call(
                "file-edit",
                "fs.edit_file",
                {"path": str(file), "edits": [{"oldText": "alpha", "newText": "beta"}]},
            )
        )["state"] == "succeeded"
        result = await call("file-read", "fs.read_file", {"path": str(file)})
        assert result["state"] == "succeeded"
        assert result["result"]["data"]["content"] == "beta\n"
        result = await call(
            "missing-read", "fs.read_file", {"path": str(tmp_path / "does-not-exist")}
        )
        assert result["state"] == "failed"


async def test_slow_fs_does_not_block_unrelated_api(live_server, tmp_path):
    import threading

    _, _, http = live_server
    executor = LocalExecutor(tmp_path / "client", "laptop-one")
    started = threading.Event()
    release = threading.Event()
    original = executor.files["fs.write_file"]

    def slow_write(**arguments):
        started.set()
        release.wait(5)
        return original(**arguments)

    executor.files["fs.write_file"] = slow_write
    async with running_client(live_server, tmp_path / "client", executor):
        await http.post(
            "/v1/operations",
            json=payload(arguments={"path": str(tmp_path / "file"), "content": "x"}),
        )
        assert await asyncio.to_thread(started.wait, 5)
        try:
            async with asyncio.timeout(0.5):
                response = await http.get("/v1/devices")
            assert response.status_code == 200
            assert not release.is_set()
        finally:
            release.set()
        assert (await terminal(http, "op-net"))["state"] == "succeeded"


async def test_bash_failure_and_timeout_are_terminal(live_server, tmp_path):
    _, _, http = live_server
    executor = LocalExecutor(tmp_path / "client", "laptop-one")
    async with running_client(live_server, tmp_path / "client", executor):
        await http.post(
            "/v1/operations",
            json=payload(
                "exit-fail",
                "bash.execute",
                {
                    "mode": "run",
                    "argv": [sys.executable, "-c", "raise SystemExit(7)"],
                    "cwd": str(tmp_path),
                },
            ),
        )
        failed = await terminal(http, "exit-fail")
        assert failed["state"] == "failed" and failed["result"]["data"]["exit_code"] == 7
        await http.post(
            "/v1/operations",
            json=payload(
                "timeout",
                "bash.execute",
                {
                    "mode": "run",
                    "argv": [sys.executable, "-c", "import time; time.sleep(10)"],
                    "cwd": str(tmp_path),
                    "timeout": 1,
                    "timeout_behavior": "kill",
                },
            ),
        )
        failed = await terminal(http, "timeout")
        assert failed["state"] == "failed"
        assert "background_pid" not in failed["result"]["data"]
        assert not executor.runtime._active


async def test_persistence_failure_is_fatal_not_silent_success(live_server, tmp_path, monkeypatch):
    _, _, http = live_server
    with pytest.raises(OSError, match="intentional journal failure"):
        async with running_client(live_server, tmp_path / "client", FakeExecutor()) as client:
            original = client.journal.update

            async def fail_terminal(operation_id, state, result=None, **kwargs):
                if state == "succeeded":
                    raise OSError("intentional journal failure")
                return await original(operation_id, state, result, **kwargs)

            monkeypatch.setattr(client.journal, "update", fail_terminal)
            await http.post("/v1/operations", json=payload())
            await asyncio.wait_for(client._closed.wait(), 5)
    assert (await http.get("/v1/operations/op-net")).json()["state"] == "unknown"


async def test_live_submit_wait_returns_result(live_server, tmp_path):
    _, _, http = live_server
    executor = FakeExecutor()
    async with running_client(live_server, tmp_path / "client", executor):
        response = await http.post("/v1/operations?wait_timeout_ms=1500", json=payload())
        assert response.status_code == 200
        assert response.json()["state"] == "succeeded"
        assert response.json() == (await http.get("/v1/operations/op-net")).json()
        again = await http.post("/v1/operations?wait_timeout_ms=5", json=payload())
        assert again.json() == response.json()
        assert executor.calls == 1


@pytest.mark.parametrize("value", ["-1", "5001", "1.5", "true"])
async def test_live_invalid_wait_has_no_side_effect(live_server, value):
    _, _, http = live_server
    response = await http.post("/v1/operations?wait_timeout_ms=" + value, json=payload())
    assert response.status_code == 422
    assert (await http.get("/v1/operations/op-net")).status_code == 404


async def test_live_wait_expiry_preserves_execution(live_server, tmp_path):
    _, _, http = live_server
    executor = FakeExecutor(paused=True)
    async with running_client(live_server, tmp_path / "client", executor):
        response = await http.post("/v1/operations?wait_timeout_ms=5", json=payload())
        assert response.json()["state"] in {"sent", "running"}
        await executor.started.wait()
        executor.release.set()
        assert (await terminal(http, "op-net"))["state"] == "succeeded"
        assert executor.calls == 1
