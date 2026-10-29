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
    monkeypatch.setattr(socket, "MSG_CMSG_CLOEXEC", getattr(socket, "MSG_CMSG_CLOEXEC", 0x40000000), raising=False)


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


def test_credential_frame_receives_descriptors_atomically_close_on_exec(linux_credentials, monkeypatch):
    monkeypatch.setattr(socket, "MSG_CMSG_CLOEXEC", 0x40000000, raising=False)
    class Capture(Connection):
        def recvmsg(self, *args):
            assert len(args) == 3 and args[2] == socket.MSG_CMSG_CLOEXEC
            return part(b'{}\n', credential())
    data, identity = helper._receive_root_credential_frame(Capture([]), deadline=helper.time.monotonic() + 5)
    assert data == b'{}\n' and identity == (123, 0, 0)


def test_credential_frame_refuses_when_atomic_cloexec_is_unavailable(linux_credentials, monkeypatch):
    monkeypatch.delattr(socket, "MSG_CMSG_CLOEXEC", raising=False)
    with pytest.raises(helper.HelperError, match="credentials unavailable"):
        helper._receive_root_credential_frame(Connection([]), deadline=helper.time.monotonic() + 5)


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


def test_query_listener_sets_credentials_before_bind_and_holds_daemon_lease(linux_credentials, monkeypatch, tmp_path):
    from contextlib import contextmanager

    events = []
    monkeypatch.setattr(helper.sys, "platform", "linux")
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(socket, "SO_PASSCRED", getattr(socket, "SO_PASSCRED", 16), raising=False)
    monkeypatch.setattr(helper, "_trusted", lambda *_a, **_k: None)
    monkeypatch.setattr(helper, "_path", lambda value: value)
    monkeypatch.setattr(helper, "Helper", lambda registry: ("service", registry))

    @contextmanager
    def lease(_registry):
        events.append("lock")
        try:
            yield 42
        finally:
            events.append("unlock")

    class Listener:
        def __enter__(self): return self
        def __exit__(self, *_args): events.append("close")
        def setsockopt(self, *_args): events.append("credentials")
        def bind(self, _path): events.append("bind")
        def listen(self, _backlog): events.append("listen")
        def accept(self):
            events.append("accept")
            raise RuntimeError("stop test")

    monkeypatch.setattr(helper, "_daemon_lease", lease)
    # Drain check is covered separately; these tests assert credential/lock order.
    monkeypatch.setattr(helper, "_retired_reason", lambda: None)
    monkeypatch.setattr(socket, "socket", lambda *_args: Listener())
    monkeypatch.setattr(helper.os, "chown", lambda *_args: events.append("chown"))
    monkeypatch.setattr(helper.os, "chmod", lambda *_args: events.append("chmod"))
    with pytest.raises(RuntimeError, match="stop test"):
        helper._serve_credential_queries(
            tmp_path, tmp_path / "query.sock", group=0
        )
    assert events == ["lock", "credentials", "bind", "chown", "chmod", "listen", "accept", "close", "unlock"]


def test_query_locked_loop_does_not_reacquire_daemon_lock(linux_credentials, monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper, "_path", lambda value: value)
    monkeypatch.setattr(helper, "_trusted", lambda *_a, **_k: None)
    monkeypatch.setattr(helper, "Helper", lambda _registry: object())
    monkeypatch.setattr(helper, "_daemon_lease", lambda *_a: pytest.fail("unexpected second lock"))
    monkeypatch.setattr(helper, "_retired_reason", lambda: None)
    monkeypatch.setattr(helper.os, "chown", lambda *_a: None)
    monkeypatch.setattr(helper.os, "chmod", lambda *_a: None)

    class Listener:
        def __enter__(self): return self
        def __exit__(self, *_args): events.append("close")
        def setsockopt(self, *_args): events.append("credentials")
        def bind(self, _path): events.append("bind")
        def listen(self, _backlog): events.append("listen")
        def accept(self): raise RuntimeError("end")

    monkeypatch.setattr(socket, "socket", lambda *_a: Listener())
    with pytest.raises(RuntimeError, match="end"):
        helper._serve_credential_queries_locked(tmp_path, tmp_path / "query.sock", group=0)
    assert events == ["credentials", "bind", "listen", "close"]


def test_query_listener_rejects_unsupported_platform_before_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(helper.sys, "platform", "darwin")
    monkeypatch.setattr(helper, "_daemon_lease", lambda *_args: pytest.fail("must not lock"))
    with pytest.raises(helper.HelperError, match="requires Linux"):
        helper._serve_credential_queries(tmp_path, tmp_path / "query.sock", group=0)


class QueryConnection:
    def __init__(self):
        self.closed = False
        self.sent = []
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        self.sent.append(data)


def test_credential_query_returns_one_response_and_closes(monkeypatch):
    import json

    conn = QueryConnection()
    request = {"action": "query", "instance_id": "probe", "operation_id": "one"}
    monkeypatch.setattr(helper, "_receive_root_credential_frame", lambda *_a, **_k: (
        b'{"action":"query","instance_id":"probe","operation_id":"one"}\n', (123, 0, 0)
    ))
    class Service:
        def handle(self, value, *, peer_uid):
            assert not conn.closed
            assert value == request and peer_uid == 0
            return {"result": "observed"}
    helper._serve_credential_query_once(Service(), conn, deadline=10)
    assert conn.closed and conn.timeout == 5 and len(conn.sent) == 1
    assert json.loads(conn.sent[0]) == {"ok": True, "result": {"result": "observed"}}


@pytest.mark.parametrize("frame", [
    b'{"action":"start","instance_id":"probe","operation_id":"one"}\n',
    b'{"action":"query","action":"start","instance_id":"probe","operation_id":"one"}\n',
    b'{"action":"query","instance_id":"../probe","operation_id":"one"}\n',
    b'{"action":"query","instance_id":"probe","operation_id":"one","uid":0}\n',
    b'[]\n', b'{broken}\n', b'{"action":NaN}\n',
])
def test_credential_query_invalid_request_never_calls_handler(monkeypatch, frame):
    import json

    conn = QueryConnection()
    monkeypatch.setattr(helper, "_receive_root_credential_frame", lambda *_a, **_k: (frame, (123, 0, 0)))
    class Service:
        def handle(self, *_a, **_kw):
            pytest.fail("invalid request reached handler")
    helper._serve_credential_query_once(Service(), conn, deadline=10)
    assert conn.closed and len(conn.sent) == 1
    assert json.loads(conn.sent[0]) == {"ok": False, "error": "helper_request_refused"}


def test_credential_query_receive_failure_never_calls_handler(monkeypatch):
    import json

    conn = QueryConnection()
    def fail(*_a, **_kw):
        raise helper.HelperError("sensitive failure")
    monkeypatch.setattr(helper, "_receive_root_credential_frame", fail)
    class Service:
        def handle(self, *_a, **_kw):
            pytest.fail("invalid request reached handler")
    helper._serve_credential_query_once(Service(), conn, deadline=10)
    assert conn.closed and json.loads(conn.sent[0]) == {"ok": False, "error": "helper_request_refused"}


def test_credential_query_lost_response_does_not_repeat_handler(monkeypatch):
    class LostConnection(QueryConnection):
        def sendall(self, _data):
            raise OSError("lost response")

    conn = LostConnection()
    calls = []
    monkeypatch.setattr(helper, "_receive_root_credential_frame", lambda *_a, **_k: (
        b'{"action":"query","instance_id":"probe","operation_id":"one"}\n', (123, 0, 0)
    ))
    class Service:
        def handle(self, value, *, peer_uid):
            calls.append((value, peer_uid))
            return {"observed": True}
    helper._serve_credential_query_once(Service(), conn, deadline=10)
    assert conn.closed and len(calls) == 1
