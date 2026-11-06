import asyncio
import json
from types import SimpleNamespace

import pytest

from device_runtime.client import DeviceClient
from device_runtime.maintenance import IdleLease, MaintenanceServer, idle_lease


@pytest.fixture
def client(tmp_path):
    async def active_count():
        return 0

    obj = DeviceClient(
        server_url="ws://localhost:9001",
        device_id="test",
        token="t" * 32,
        state_dir=tmp_path,
        executor=SimpleNamespace(tasks=SimpleNamespace(active_count=active_count)),
    )
    obj._initialized = True
    return obj


@pytest.mark.asyncio
async def test_commit_is_irreversible_after_disconnect(client):
    calls = []
    client._maintenance_exit_callback = lambda: calls.append("exit")
    assert (await client.maintenance_acquire("lease"))["acquired"]
    assert await client.maintenance_exit("lease") == {"exit_committed": True}
    await client.maintenance_release("lease")
    assert client._closed.is_set()
    assert client._maintenance_id == "lease"
    assert calls == ["exit"]
    with pytest.raises(ValueError):
        await client.maintenance_exit("lease")
    with pytest.raises(ValueError):
        await client.maintenance_resume("lease")
    with pytest.raises(ValueError):
        await client.maintenance_acquire("new")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ["disabled", "wrong_owner", "released", "foreground", "background"]
)
async def test_invalid_exit_never_schedules_stop(client, reason):
    calls = []
    client._maintenance_exit_callback = lambda: calls.append("exit")
    await client.maintenance_acquire("lease")
    owner = "lease"
    if reason == "disabled":
        client._maintenance_exit_callback = None
    elif reason == "wrong_owner":
        owner = "other"
    elif reason == "released":
        await client.maintenance_release(owner)
    elif reason == "foreground":
        client._tasks.add(object())
    else:

        async def active():
            return 1

        client.executor.tasks.active_count = active
    with pytest.raises(ValueError):
        await client.maintenance_exit(owner)
    assert calls == []
    assert not client._closed.is_set()
    assert not client._maintenance_exit_committed


@pytest.mark.asyncio
async def test_request_uses_acquired_connection_only(client, tmp_path):
    calls = []
    client._maintenance_exit_callback = lambda: calls.append("exit")
    server = MaintenanceServer(client, tmp_path / "control.sock")
    await server.start()
    try:

        def request():
            with idle_lease(server.path) as lease:
                assert lease.request_exit() == {"exit_committed": True}
                with pytest.raises(ValueError, match="already attempted"):
                    lease.request_exit()

        await asyncio.to_thread(request)
        assert calls == ["exit"]
        assert client._closed.is_set()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_disconnect_without_exit_restores_admission(client, tmp_path):
    calls = []
    client._maintenance_exit_callback = lambda: calls.append("exit")
    server = MaintenanceServer(client, tmp_path / "control.sock")
    await server.start()
    try:

        def abandon():
            with idle_lease(server.path):
                pass

        await asyncio.to_thread(abandon)
        async with asyncio.timeout(2):
            while client._maintenance_id is not None:
                await asyncio.sleep(0.01)
        assert calls == []
        assert not client._closed.is_set()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_separate_socket_cannot_exit_lease_owner(client, tmp_path):
    calls = []
    client._maintenance_exit_callback = lambda: calls.append("exit")
    server = MaintenanceServer(client, tmp_path / "control.sock")
    await server.start()
    reader, writer = await asyncio.open_unix_connection(server.path)
    try:
        writer.write(b'{"action":"acquire"}\n')
        await writer.drain()
        assert json.loads(await reader.readline())["acquired"]
        r2, w2 = await asyncio.open_unix_connection(server.path)
        w2.write(b'{"action":"exit"}\n')
        await w2.drain()
        assert "error" in json.loads(await r2.readline())
        w2.close()
        await w2.wait_closed()
        assert calls == []
    finally:
        writer.close()
        await writer.wait_closed()
        await server.close()


@pytest.mark.parametrize("response", [b"", b"{}\n", b'{"exit_committed":1}\n', b"invalid\n"])
def test_response_loss_never_reconnects_or_replays(response):
    class Connection:
        sends = 0

        def sendall(self, data):
            assert data == b"X"
            self.sends += 1

        def recv(self, size):
            return response

    connection = Connection()
    lease = IdleLease(connection)
    with pytest.raises(ValueError, match="unknown"):
        lease.request_exit()
    with pytest.raises(ValueError, match="already attempted"):
        lease.request_exit()
    assert connection.sends == 1


@pytest.mark.asyncio
async def test_run_cancellation_waits_for_full_executor_cleanup(client, monkeypatch):
    from contextlib import asynccontextmanager

    from device_runtime import client as module

    connected = asyncio.Event()
    cleanup_entered = asyncio.Event()
    finish_cleanup = asyncio.Event()
    events = []

    class Socket:
        async def send(self, message):
            pass

        async def recv(self):
            return json.dumps({"v": 1, "type": "welcome", "device_id": "test"})

        def __aiter__(self):
            return self

        async def __anext__(self):
            connected.set()
            await asyncio.Event().wait()

        async def close(self, **kwargs):
            events.append("socket_closed")

    @asynccontextmanager
    async def connect(*args, **kwargs):
        socket = Socket()
        try:
            yield socket
        finally:
            await socket.close()

    async def noop(*args):
        pass

    async def executor_close():
        cleanup_entered.set()
        await finish_cleanup.wait()
        events.append("executor_closed")

    async def journal_close():
        events.append("journal_closed")

    client.executor.capabilities = []
    client.executor.aclose = executor_close
    monkeypatch.setattr(module, "connect", connect)
    monkeypatch.setattr(client.journal, "initialize", noop)
    monkeypatch.setattr(client.journal, "disconnect", noop)
    monkeypatch.setattr(client.journal, "close", journal_close)
    monkeypatch.setattr(client, "_reconcile", noop)
    task = asyncio.create_task(client.run())
    client._maintenance_exit_callback = lambda: asyncio.get_running_loop().call_soon(task.cancel)
    try:
        async with asyncio.timeout(2):
            await connected.wait()
            assert (await client.maintenance_acquire("owner"))["acquired"]
            assert (await client.maintenance_exit("owner"))["exit_committed"]
            await cleanup_entered.wait()
            assert not task.done()
            await client.maintenance_release("owner")
            assert client._closed.is_set()
            finish_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert events == ["socket_closed", "executor_closed", "journal_closed"]
    finally:
        finish_cleanup.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
