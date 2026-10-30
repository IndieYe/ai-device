import array
import os
import socket
import struct

import pytest

from device_runtime import native_bootstrap as bootstrap


@pytest.fixture
def linux_socket(monkeypatch):
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(socket, "SO_PASSCRED", getattr(socket, "SO_PASSCRED", 16), raising=False)
    monkeypatch.setattr(socket, "SCM_CREDENTIALS", getattr(socket, "SCM_CREDENTIALS", 2), raising=False)


class FakeSocket:
    def __init__(self, data, controls, flags=0, enabled=1, kind=socket.SOCK_SEQPACKET):
        self.record = (data, controls, flags, None)
        self.enabled = enabled
        self.kind = kind
        self.timeout = None

    def getsockopt(self, _level, option):
        return self.enabled if option == socket.SO_PASSCRED else self.kind

    def settimeout(self, value):
        self.timeout = value

    def recvmsg(self, *_args):
        return self.record


def credential(pid=42, uid=0, gid=0):
    return (socket.SOL_SOCKET, getattr(socket, "SCM_CREDENTIALS", 2), struct.pack("3i", pid, uid, gid))


def test_handshake_accepts_matching_record_and_checks_pidfd_twice(linux_socket, monkeypatch):
    checks = []
    monkeypatch.setattr(bootstrap, "_handshake_pidfd_alive", lambda fd: checks.append(fd) or True)
    challenge = b"x" * 32
    conn = FakeSocket(b"READY:" + challenge, [credential()])
    result = bootstrap._observe_child_handshake(conn, 7, 42, challenge, deadline=bootstrap.time.monotonic()+5)
    assert result == "observed_not_authorized"
    assert checks == [7, 7] and 0 < conn.timeout <= 5


@pytest.mark.parametrize("controls,flags,data", [
    ([], 0, b"READY:" + b"x" * 32),
    ([credential(pid=43)], 0, b"READY:" + b"x" * 32),
    ([credential(uid=1)], 0, b"READY:" + b"x" * 32),
    ([credential(), credential()], 0, b"READY:" + b"x" * 32),
    ([credential()], socket.MSG_CTRUNC, b"READY:" + b"x" * 32),
    ([credential()], socket.MSG_TRUNC, b"READY:" + b"x" * 32),
    ([credential()], 0, b"READY:" + b"y" * 32),
    ([credential()], 0, b""),
])
def test_handshake_rejects_mismatched_records(linux_socket, monkeypatch, controls, flags, data):
    monkeypatch.setattr(bootstrap, "_handshake_pidfd_alive", lambda _fd: True)
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap._observe_child_handshake(FakeSocket(data, controls, flags), 7, 42, b"x"*32, deadline=bootstrap.time.monotonic()+5)


@pytest.mark.parametrize("states", [[False], [True, False]])
def test_handshake_refuses_exited_child_before_or_after_receive(linux_socket, monkeypatch, states):
    values = iter(states)
    monkeypatch.setattr(bootstrap, "_handshake_pidfd_alive", lambda _fd: next(values))
    with pytest.raises(bootstrap.BootstrapError, match="child"):
        bootstrap._observe_child_handshake(FakeSocket(b"READY:"+b"x"*32, [credential()]), 7, 42, b"x"*32, deadline=bootstrap.time.monotonic()+5)


def test_handshake_closes_unexpected_descriptors(linux_socket, monkeypatch):
    monkeypatch.setattr(bootstrap, "_handshake_pidfd_alive", lambda _fd: True)
    fd = os.open(os.devnull, os.O_RDONLY)
    rights = (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]).tobytes())
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap._observe_child_handshake(FakeSocket(b"READY:"+b"x"*32, [credential(), rights], socket.MSG_CTRUNC), 7, 42, b"x"*32, deadline=bootstrap.time.monotonic()+5)
    with pytest.raises(OSError):
        os.fstat(fd)


@pytest.mark.parametrize("enabled,kind", [(0, socket.SOCK_SEQPACKET), (1, socket.SOCK_STREAM)])
def test_handshake_rejects_unprepared_channel(linux_socket, enabled, kind):
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap._observe_child_handshake(FakeSocket(b"", [], enabled=enabled, kind=kind), 7, 42, b"x"*32, deadline=bootstrap.time.monotonic()+5)


def test_handshake_rejects_expired_deadline_and_receive_timeout(linux_socket, monkeypatch):
    monkeypatch.setattr(bootstrap, "_handshake_pidfd_alive", lambda _fd: True)
    with pytest.raises(bootstrap.BootstrapError, match="deadline"):
        bootstrap._observe_child_handshake(FakeSocket(b"", []), 7, 42, b"x"*32, deadline=bootstrap.time.monotonic()-1)
    class TimedOut(FakeSocket):
        def recvmsg(self, *_args):
            raise TimeoutError("expired")
    with pytest.raises(bootstrap.BootstrapError, match="failed"):
        bootstrap._observe_child_handshake(TimedOut(b"", []), 7, 42, b"x"*32, deadline=bootstrap.time.monotonic()+5)


@pytest.mark.parametrize("pid,challenge", [(True, b"x"*32), (0, b"x"*32), (42, b"short")])
def test_handshake_rejects_invalid_input(linux_socket, pid, challenge):
    with pytest.raises(bootstrap.BootstrapError, match="inputs"):
        bootstrap._observe_child_handshake(FakeSocket(b"", []), 7, pid, challenge, deadline=bootstrap.time.monotonic()+5)


def test_handshake_close_error_does_not_skip_other_descriptors(linux_socket, monkeypatch):
    monkeypatch.setattr(bootstrap, "_handshake_pidfd_alive", lambda _fd: True)
    first = os.open(os.devnull, os.O_RDONLY)
    second = os.open(os.devnull, os.O_RDONLY)
    real_close = os.close
    calls = []
    def close(fd):
        calls.append(fd)
        real_close(fd)
        if fd == first:
            raise OSError("injected")
    monkeypatch.setattr(bootstrap.os, "close", close)
    rights = (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [first, second]).tobytes())
    with pytest.raises(bootstrap.BootstrapError, match="cannot close"):
        bootstrap._observe_child_handshake(FakeSocket(b"x", [rights]), 7, 42, b"x"*32, deadline=bootstrap.time.monotonic()+5)
    assert calls == [first, second]
    for fd in (first, second):
        with pytest.raises(OSError):
            os.fstat(fd)
