import asyncio
import os
import struct
import sys
from types import SimpleNamespace

import pytest

from device_runtime.client import DeviceClient
from device_runtime.client_macos_lease import bind_lease, sample_peer
from device_runtime.client_macos_processes import ProcessIdentity
from device_runtime.maintenance import IdleLease, MaintenanceServer, idle_lease


@pytest.mark.parametrize("failure", [None, "changed", "eof", "mismatch", "absent"])
def test_challenge_identity_binding(failure):
    class Connection:
        def __init__(self):
            self.sent = []

        def sendall(self, data):
            self.sent.append(data)

        def recv(self, limit):
            if failure == "eof":
                return b""
            if failure == "mismatch":
                return b"x" * limit
            return self.sent[0][1:]

    connection = Connection()
    lease = IdleLease(connection)
    samples = iter(
        [
            None if failure == "absent" else ProcessIdentity(123, 10),
            ProcessIdentity(123, 11 if failure == "changed" else 10),
        ]
    )
    if failure:
        with pytest.raises(ValueError, match="binding unknown"):
            lease.bind_peer(lambda _: next(samples))
        with pytest.raises(ValueError, match="already attempted"):
            lease.request_exit()
        assert all(not data.startswith(b"X") for data in connection.sent)
    else:
        assert lease.bind_peer(lambda _: next(samples)) == ProcessIdentity(123, 10)
        assert len(connection.sent[0]) == 33
        with pytest.raises(ValueError, match="already attempted"):
            lease.bind_peer(lambda _: pytest.fail("resampled"))


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin socket peer PID")
@pytest.mark.asyncio
async def test_real_local_socket_binds_then_exits(tmp_path):
    async def active():
        return 0

    client = DeviceClient(
        server_url="ws://localhost:9001",
        device_id="test",
        token="t" * 32,
        state_dir=tmp_path,
        executor=SimpleNamespace(tasks=SimpleNamespace(active_count=active)),
    )
    client._initialized = True
    calls = []
    client._maintenance_exit_callback = lambda: calls.append("exit")
    server = MaintenanceServer(client, tmp_path / "control.sock")
    await server.start()
    try:

        def request():
            with idle_lease(server.path) as lease:
                identity = bind_lease(lease)
                assert identity.pid == os.getpid()
                assert lease.request_exit() == {"exit_committed": True}
                return identity

        identity = await asyncio.to_thread(request)
        assert identity.started > 0
        assert calls == ["exit"]
    finally:
        await server.close()


def test_peer_sample_uses_kernel_pid(monkeypatch):
    from device_runtime import client_macos_lease as module

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module, "inspect_identity", lambda pid: ProcessIdentity(pid, 10))

    class Connection:
        def getsockopt(self, level, option, size):
            assert (level, option, size) == (0, 2, struct.calcsize("i"))
            return struct.pack("i", 123)

    assert sample_peer(Connection()) == ProcessIdentity(123, 10)


@pytest.mark.parametrize("failure", [None, "binding", "request", "wait"])
def test_original_exit_chain_does_not_reconnect_or_replay(monkeypatch, failure):
    from device_runtime import client_macos_lease as module
    from device_runtime import client_macos_processes as processes

    events = []
    identity = ProcessIdentity(123, 10)

    def bind(lease):
        events.append("bind")
        if failure == "binding":
            raise ValueError("unknown binding")
        return identity

    class Lease:
        def request_exit(self):
            events.append("request")
            if failure == "request":
                raise ValueError("unknown commitment")

    def wait(value, *, timeout):
        assert value == identity
        assert timeout == 4
        events.append("wait")
        if failure == "wait":
            raise ValueError("unknown exit")

    monkeypatch.setattr(module, "bind_lease", bind)
    monkeypatch.setattr(processes, "wait_original_exit", wait)
    if failure:
        with pytest.raises(ValueError):
            module.request_original_exit(Lease(), timeout=4)
    else:
        assert module.request_original_exit(Lease(), timeout=4) == identity
    expected = (
        ["bind"]
        if failure == "binding"
        else ["bind", "request"]
        if failure == "request"
        else ["bind", "request", "wait"]
    )
    assert events == expected
