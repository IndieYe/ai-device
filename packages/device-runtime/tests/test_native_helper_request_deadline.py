"""Real socket regression for the helper's serial request reader."""

import socket
import struct
import threading
import time
from pathlib import Path

import pytest

from device_runtime import native_helper as nh


@pytest.mark.parametrize("slow", [False, True])
def test_request_read_deadline(monkeypatch, tmp_path, slow):
    server_end, client = socket.socketpair()
    calls = []
    failures = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            server_end.close()

        def settimeout(self, value):
            server_end.settimeout(value)

        def getsockopt(self, *args):
            return struct.pack("3i", 123, 12345, 12345)

        def recv(self, size):
            return server_end.recv(size)

        def sendall(self, data):
            server_end.sendall(data)

    class Listener:
        accepted = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, path):
            pass

        def listen(self, backlog):
            pass

        def accept(self):
            if self.accepted:
                raise StopIteration
            self.accepted = True
            return Connection(), None

    class Helper:
        def __init__(self, registry):
            pass

        def handle(self, request, *, peer_uid):
            calls.append((request, peer_uid))
            return {"state": "fixture"}

    monkeypatch.setattr(nh.sys, "platform", "linux")
    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)
    monkeypatch.setattr(nh, "_trusted", lambda *args, **kwargs: None)
    monkeypatch.setattr(nh.os, "chown", lambda *args: None)
    monkeypatch.setattr(nh.os, "chmod", lambda *args: None)
    monkeypatch.setattr(nh.socket, "SO_PEERCRED", 17, raising=False)
    monkeypatch.setattr(nh.socket, "socket", lambda *args, **kwargs: Listener())
    monkeypatch.setattr(nh, "Helper", Helper)

    def serve():
        try:
            nh._serve(Path(tmp_path), Path(tmp_path) / "helper.sock", group=1)
        except StopIteration:
            pass
        except Exception as exc:  # noqa: BLE001 - report worker-thread failures to the test
            failures.append(exc)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        for _ in range(11 if slow else 0):
            try:
                client.sendall(b" ")
            except (BrokenPipeError, ConnectionResetError):
                break
            time.sleep(0.5)
        try:
            client.sendall(b'{"action":"query","instance_id":"x","operation_id":"y"}\n')
        except (BrokenPipeError, ConnectionResetError):
            pass
        worker.join(3)
        assert not worker.is_alive()
        assert not failures
        if slow:
            assert calls == []
        else:
            assert calls == [({"action": "query", "instance_id": "x", "operation_id": "y"}, 12345)]
    finally:
        client.close()
        worker.join(6)
