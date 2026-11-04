import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from device_runtime.client import DeviceClient
from device_runtime.maintenance import MaintenanceServer


@pytest.fixture
def client(tmp_path):
    async def active_count():
        return 0

    obj = DeviceClient(
        server_url="ws://localhost:8765",
        device_id="test-one",
        token="test-only-client-token",
        state_dir=tmp_path,
        executor=SimpleNamespace(tasks=SimpleNamespace(active_count=active_count)),
    )
    obj._initialized = True
    return obj


@pytest.mark.asyncio
async def test_busy_does_not_pause(client):
    client._tasks.add(object())
    result = await client.maintenance_acquire("owner")
    assert not result["acquired"]
    assert client._maintenance_id is None


@pytest.mark.asyncio
async def test_background_busy_does_not_pause(client):
    async def active_count():
        return 1

    client.executor.tasks.active_count = active_count
    assert not (await client.maintenance_acquire("owner"))["acquired"]
    assert client._maintenance_id is None


@pytest.mark.asyncio
async def test_only_owner_releases(client):
    assert (await client.maintenance_acquire("one"))["acquired"]
    with pytest.raises(ValueError, match="already held"):
        await client.maintenance_acquire("two")
    await client.maintenance_release("two")
    assert client._maintenance_id == "one"
    await client.maintenance_release("one")
    assert client._maintenance_id is None


@pytest.mark.asyncio
async def test_socket_disconnect_releases_pause(client):
    # A short path also runs on macOS's limited Unix socket address length.
    with tempfile.TemporaryDirectory() as directory:
        server = MaintenanceServer(client, Path(directory) / "control.sock")
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(server.path)
            writer.write(b'{"action":"acquire"}\n')
            await writer.drain()
            assert json.loads(await reader.readline())["acquired"]
            assert client._maintenance_id is not None
            writer.close()
            await writer.wait_closed()
            async with asyncio.timeout(2):
                while client._maintenance_id is not None:
                    await asyncio.sleep(0.01)
        finally:
            await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("busy,fail_stop", [(False, False), (False, True), (True, False)])
async def test_installer_lease_lifetime(client, busy, fail_stop):
    from device_runtime.maintenance import ClientBusy, idle_lease

    if busy:
        client._tasks.add(object())
    with tempfile.TemporaryDirectory() as directory:
        server = MaintenanceServer(client, Path(directory) / "control.sock")
        await server.start()
        entered = []

        def install():
            with idle_lease(server.path):
                entered.append(True)
                assert client._maintenance_id is not None
                if fail_stop:
                    raise RuntimeError("simulated service stop failure")

        try:
            if busy:
                with pytest.raises(ClientBusy):
                    await asyncio.to_thread(install)
                assert not entered
            elif fail_stop:
                with pytest.raises(RuntimeError, match="stop failure"):
                    await asyncio.to_thread(install)
            else:
                await asyncio.to_thread(install)
            async with asyncio.timeout(2):
                while client._maintenance_id is not None:
                    await asyncio.sleep(0.01)
        finally:
            await server.close()


@pytest.mark.asyncio
async def test_service_shutdown_closes_installer_lease(client):
    with tempfile.TemporaryDirectory() as directory:
        server = MaintenanceServer(client, Path(directory) / "control.sock")
        await server.start()
        reader, writer = await asyncio.open_unix_connection(server.path)
        try:
            writer.write(b'{"action":"acquire"}\n')
            await writer.drain()
            assert json.loads(await reader.readline())["acquired"]
            async with asyncio.timeout(2):
                await server.close()
                assert await reader.read() == b""
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()


@pytest.mark.asyncio
async def test_idle_check_serializes_new_request_admission(client):
    counting = asyncio.Event()
    finish_count = asyncio.Event()
    accepting = asyncio.Event()
    executed = asyncio.Event()
    journal_calls = []
    reports = []

    async def active_count():
        counting.set()
        await finish_count.wait()
        return 0

    async def accept(operation, state):
        journal_calls.append(operation.operation_id)
        return {}, True

    async def update(operation_id, state, result):
        return {"operation_id": operation_id, "state": state, "result": result}

    async def report(record):
        reports.append(record)

    async def execute(operation):
        executed.set()

    client.executor.tasks.active_count = active_count
    client.journal = SimpleNamespace(accept=accept, update=update)
    client._report = report
    client._execute = execute
    operation = SimpleNamespace(
        device_id=client.device_id, operation_id="lease-race", action="bash.execute"
    )

    async def submit():
        accepting.set()
        await client._accept(operation)

    async with asyncio.timeout(3):
        lease = asyncio.create_task(client.maintenance_acquire("installer"))
        await counting.wait()
        request = asyncio.create_task(submit())
        await accepting.wait()
        assert not request.done()
        assert journal_calls == []
        assert not executed.is_set()
        finish_count.set()
        assert (await lease)["acquired"]
        await request
        assert not executed.is_set()
        assert reports[0]["result"]["data"]["error"] == "client_maintenance"
        await client.maintenance_release("installer")
        operation.operation_id = "after-release"
        await client._accept(operation)
        await executed.wait()
        await asyncio.gather(*client._tasks)
