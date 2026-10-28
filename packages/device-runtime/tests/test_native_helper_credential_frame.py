import array
import os
import socket
import struct

import pytest

from device_runtime import native_helper as helper


@pytest.fixture
def linux_credentials(monkeypatch):
    monkeypatch.setattr(helper.sys, "platform", "linux")
    monkeypatch.setattr(socket, "SO_PASSCRED", getattr(socket, "SO_PASSCRED", 16), raising=False)
    monkeypatch.setattr(socket, "SCM_CREDENTIALS", getattr(socket, "SCM_CREDENTIALS", 2), raising=False)


class Connection:
    def __init__(self, parts, *, enabled=1):
        self.parts = iter(parts)
        self.enabled = enabled
        self.timeouts = []

    def getsockopt(self, *_args):
        return self.enabled

    def settimeout(self, value):
        self.timeouts.append(value)

    def recvmsg(self, *_args):
        return next(self.parts)


def credential(pid=123, uid=0, gid=0):
    return (socket.SOL_SOCKET, socket.SCM_CREDENTIALS, struct.pack("3i", pid, uid, gid))


def part(data, *controls, flags=0):
    return data, list(controls), flags, None


def test_credential_frame_reassembles_only_same_root_sender(linux_credentials):
    conn = Connection([part(b'{"action":', credential()), part(b'"query"}\n', credential())])
    frame, identity = helper._receive_root_credential_frame(conn, deadline=helper.time.monotonic() + 5)
    assert frame == b'{"action":"query"}\n'
    assert identity == (123, 0, 0)
    assert len(conn.timeouts) == 2 and all(0 < value <= 5 for value in conn.timeouts)


@pytest.mark.parametrize("parts", [
    lambda: [part(b"request\n")],
    lambda: [part(b"request\n", credential(uid=7))],
    lambda: [part(b"a", credential()), part(b"b\n", credential(pid=124))],
    lambda: [part(b"request\n", credential(), credential())],
    lambda: [part(b"request\n", (socket.SOL_SOCKET, socket.SCM_CREDENTIALS, b"short"))],
    lambda: [part(b"request\n", credential(), flags=socket.MSG_CTRUNC)],
    lambda: [part(b"request\n", credential(), flags=socket.MSG_TRUNC)],
    lambda: [part(b"a\nb\n", credential())],
    lambda: [part(b"", credential())],
    lambda: [part(b"x" * (helper.MAX_REQUEST + 1), credential())],
])
def test_credential_frame_refuses_invalid_or_mixed_input(linux_credentials, parts):
    with pytest.raises(helper.HelperError):
        helper._receive_root_credential_frame(Connection(parts()), deadline=helper.time.monotonic() + 5)


def test_credential_frame_refuses_disabled_listener_and_expired_deadline(linux_credentials):
    with pytest.raises(helper.HelperError, match="credential"):
        helper._receive_root_credential_frame(Connection([], enabled=0), deadline=helper.time.monotonic() + 5)
    with pytest.raises(helper.HelperError, match="deadline"):
        helper._receive_root_credential_frame(Connection([]), deadline=helper.time.monotonic() - 1)


def test_credential_frame_closes_all_delivered_descriptors_on_rejection(linux_credentials):
    first = os.open(os.devnull, os.O_RDONLY)
    second = os.open(os.devnull, os.O_RDONLY)
    payload = array.array("i", [first, second]).tobytes()
    conn = Connection([part(b"request\n", credential(), (socket.SOL_SOCKET, socket.SCM_RIGHTS, payload))])
    with pytest.raises(helper.HelperError):
        helper._receive_root_credential_frame(conn, deadline=helper.time.monotonic() + 5)
    for fd in (first, second):
        with pytest.raises(OSError):
            os.fstat(fd)


def test_credential_frame_closes_delivered_fd_even_when_control_truncated(linux_credentials):
    fd = os.open(os.devnull, os.O_RDONLY)
    rights = (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]).tobytes())
    conn = Connection([part(b"x\n", rights, flags=socket.MSG_CTRUNC)])
    with pytest.raises(helper.HelperError, match="truncated"):
        helper._receive_root_credential_frame(conn, deadline=helper.time.monotonic() + 5)
    with pytest.raises(OSError):
        os.fstat(fd)


def test_credential_frame_refuses_timeout_and_unsupported_platform(linux_credentials, monkeypatch):
    class TimedOut(Connection):
        def recvmsg(self, *_args):
            raise TimeoutError("timed out")

    with pytest.raises(helper.HelperError, match="receive failed"):
        helper._receive_root_credential_frame(TimedOut([]), deadline=helper.time.monotonic() + 5)
    monkeypatch.setattr(helper.sys, "platform", "darwin")
    with pytest.raises(helper.HelperError, match="unavailable"):
        helper._receive_root_credential_frame(Connection([]), deadline=helper.time.monotonic() + 5)


def test_credential_frame_closes_remaining_fds_after_close_error(linux_credentials, monkeypatch):
    first = os.open(os.devnull, os.O_RDONLY)
    second = os.open(os.devnull, os.O_RDONLY)
    real_close = os.close
    called = []

    def close(fd):
        called.append(fd)
        real_close(fd)
        if fd == first:
            raise OSError("injected close failure")

    monkeypatch.setattr(helper.os, "close", close)
    rights = (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [first, second]).tobytes())
    with pytest.raises(helper.HelperError, match="cannot close unexpected"):
        helper._receive_root_credential_frame(
            Connection([part(b"x\n", rights)]), deadline=helper.time.monotonic() + 5
        )
    assert called == [first, second]
    for fd in (first, second):
        with pytest.raises(OSError):
            os.fstat(fd)
