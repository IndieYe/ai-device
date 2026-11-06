import copy

import pytest

from device_runtime.client_macos_processes import ProcessIdentity
from device_runtime.client_macos_ready import MacReadyError, validate_ready

TARGET = {"version": "0.3.4", "build_id": "sha256:" + "a" * 64}
PEER = ProcessIdentity(5012, 12345.0)


def test_exact_ready_snapshot_returns_copy():
    status = {"ready": True, "pid": PEER.pid, "runtime_version": TARGET.copy()}
    before = copy.deepcopy(status)
    result = validate_ready(status, expected_version=TARGET, peer_identity=PEER)
    assert result == TARGET and result is not TARGET
    assert status == before


@pytest.mark.parametrize(
    "change",
    [
        {"ready": False},
        {"ready": 1},
        {"pid": True},
        {"pid": 5013},
        {"runtime_version": {"version": "0.3.4", "build_id": "sha256:" + "b" * 64}},
        {"runtime_version": {"version": "0.3.3", "build_id": TARGET["build_id"]}},
        {"runtime_version": None},
    ],
)
def test_ready_or_pid_or_build_mismatch_refuses(change):
    status = {"ready": True, "pid": PEER.pid, "runtime_version": TARGET.copy(), **change}
    with pytest.raises(MacReadyError):
        validate_ready(status, expected_version=TARGET, peer_identity=PEER)


@pytest.mark.parametrize(
    "target",
    [
        None,
        {},
        {"version": "0.3.4", "build_id": None},
        {"version": "0.3.4", "build_id": "unknown"},
        {**TARGET, "extra": True},
    ],
)
def test_unknown_target_never_counts_as_ready(target):
    with pytest.raises(MacReadyError):
        validate_ready(
            {"ready": True, "pid": PEER.pid, "runtime_version": target},
            expected_version=target,
            peer_identity=PEER,
        )


def test_unbound_pid_not_accepted():
    with pytest.raises(MacReadyError):
        validate_ready(
            {"ready": True, "pid": PEER.pid, "runtime_version": TARGET},
            expected_version=TARGET,
            peer_identity=PEER.pid,
        )


@pytest.mark.parametrize(
    "fault", [None, "nonce", "pid", "peer", "eof", "large", "not-ready", "bad-json"]
)
def test_same_connection_nonce_and_peer_without_lease_or_retry(fault):
    import json

    from device_runtime.client_macos_ready import query_ready

    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("closed")

        def settimeout(self, timeout):
            assert 0 < timeout <= 5

        def connect(self, path):
            calls.append(("connect", path))

        def sendall(self, data):
            request = json.loads(data)
            assert request["action"] == "status"
            calls.append("status")
            response = {
                "ready": True,
                "pid": PEER.pid,
                "runtime_version": TARGET,
                "upgrade_id": request["upgrade_id"],
            }
            if fault == "nonce":
                response["upgrade_id"] = "wrong"
            elif fault == "pid":
                response["pid"] += 1
            elif fault == "not-ready":
                response["ready"] = False
            self.data = json.dumps(response).encode() + b"\n"
            if fault == "large":
                self.data = b"x" * 4097
            elif fault == "eof":
                self.data = b""
            elif fault == "bad-json":
                self.data = b"not-json\n"

        def recv(self, size):
            part, self.data = self.data[:size], self.data[size:]
            return part

    samples = iter([PEER, ProcessIdentity(PEER.pid, PEER.started + 1) if fault == "peer" else PEER])
    connections = []

    def connect():
        connections.append(Connection())
        return connections[-1]

    if fault is None:
        assert (
            query_ready(
                "/fixture.sock",
                expected_version=TARGET,
                connect=connect,
                sample=lambda _: next(samples),
            )
            == PEER
        )
    else:
        with pytest.raises(MacReadyError):
            query_ready(
                "/fixture.sock",
                expected_version=TARGET,
                connect=connect,
                sample=lambda _: next(samples),
            )
    assert len(connections) == 1
    assert calls == [("connect", "/fixture.sock"), "status", "closed"]


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
def test_query_invalid_timeout_never_connects(timeout):
    from device_runtime.client_macos_ready import query_ready

    with pytest.raises(MacReadyError):
        query_ready(
            "/fixture.sock",
            expected_version=TARGET,
            timeout=timeout,
            connect=lambda: pytest.fail("no connection"),
        )


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="Darwin peer PID")
@pytest.mark.parametrize("matching", [True, False])
def test_real_socket_status_peer_binding_without_maintenance_acquire(tmp_path, matching):
    import json
    import select
    import subprocess
    import sys
    import tempfile

    from device_runtime.client_macos_ready import query_ready

    script = r"""
import asyncio,json,os,sys
from pathlib import Path
from device_runtime.maintenance import MaintenanceServer
async def main():
    done=asyncio.Event()
    class Client:
        async def maintenance_status(self,nonce):
            done.set()
            return {'upgrade_id':nonce,'pid':os.getpid(),'ready':True,
                    'runtime_version':json.loads(sys.argv[2])}
        async def maintenance_acquire(self,*args):
            raise AssertionError('ready must not close admission')
    server=MaintenanceServer(Client(),Path(sys.argv[1]))
    await server.start()
    print(os.getpid(),flush=True)
    try:
        await asyncio.wait_for(done.wait(),5)
        await asyncio.sleep(1.0)
    finally:
        await server.close()
asyncio.run(main())
"""
    with tempfile.TemporaryDirectory(prefix="device-ready-") as directory:
        path = __import__("pathlib").Path(directory) / "status.sock"
        child = subprocess.Popen(
            [sys.executable, "-c", script, str(path), json.dumps(TARGET)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            assert select.select([child.stdout], [], [], 5)[0]
            line = child.stdout.readline()
            assert line, child.stderr.read().decode()
            assert int(line) == child.pid
            if matching:
                peer = query_ready(
                    path, expected_version=TARGET, expected_uid=__import__("os").getuid()
                )
                assert peer.pid == child.pid
            else:
                with pytest.raises(MacReadyError):
                    query_ready(path, expected_version={**TARGET, "build_id": "sha256:" + "b" * 64})
        finally:
            _, stderr = child.communicate(timeout=8)
        assert child.returncode == 0, stderr


@pytest.mark.parametrize("fault", [None, "identity", "plist-before", "plist-after", "query"])
def test_registered_ready_rechecks_ownership(fault, tmp_path):
    from types import SimpleNamespace

    from device_runtime.client_macos_ready import query_registered_ready

    account = SimpleNamespace(layout=SimpleNamespace(state=tmp_path / "state", uid=501), uid=501)
    changed = SimpleNamespace(layout=account.layout, uid=502)
    reads = iter([account, changed if fault == "identity" else account])
    events = []

    def read():
        events.append("read")
        return next(reads)

    def validate(value):
        events.append("validate")
        if fault == "plist-before" or (fault == "plist-after" and events.count("validate") == 2):
            raise MacReadyError("invalid registered plist")

    def query(path, *, expected_version, timeout, expected_uid):
        events.append("query")
        assert path == tmp_path / "state/maintenance.sock"
        assert expected_version == TARGET and timeout == 5 and expected_uid == 501
        if fault == "query":
            raise MacReadyError("unknown query")
        return PEER

    if fault is None:
        assert (
            query_registered_ready(
                expected_version=TARGET, account_reader=read, agent_validator=validate, query=query
            )
            == PEER
        )
        assert events == ["read", "validate", "query", "read", "validate"]
    else:
        with pytest.raises(MacReadyError):
            query_registered_ready(
                expected_version=TARGET, account_reader=read, agent_validator=validate, query=query
            )
        if fault == "plist-before":
            assert "query" not in events
        assert events.count("query") <= 1


@pytest.mark.parametrize("uid", [True, 0, -1, "501"])
def test_registered_uid_invalid_before_connection(uid):
    from device_runtime.client_macos_ready import query_ready

    with pytest.raises(MacReadyError):
        query_ready(
            "/fixture",
            expected_version=TARGET,
            expected_uid=uid,
            connect=lambda: pytest.fail("no connection"),
        )


def test_wrong_peer_uid_refuses_before_status_request():
    from device_runtime.client_macos_ready import query_ready

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            pass

        def connect(self, path):
            pass

        def sendall(self, data):
            pytest.fail("no status for wrong account")

    with pytest.raises(MacReadyError, match="UID mismatch"):
        query_ready(
            "/fixture",
            expected_version=TARGET,
            expected_uid=501,
            connect=Connection,
            uid_sample=lambda _: 502,
        )
