import fcntl
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from device_runtime import native_helper as nh


class FakeSystemd:
    def __init__(self, record):
        self.record = record
        self.active = "inactive"
        self.calls = []
        self.failure = False
        self.override = {}

    def query(self, unit):
        self.calls.append(("query", unit))
        root = self.record["root"]
        return {
            "ActiveState": self.active,
            "SubState": "running",
            "MainPID": "123",
            "Job": "",
            "User": str(self.record["uid"]),
            "Group": str(self.record["gid"]),
            "FragmentPath": self.record["unit_path"],
            "ExecStart": f"{{ path={root}/run ; argv[]={root}/run client ; ignore_errors=no ; }}",
            **self.override,
        }

    def start(self, unit, *, lease_fd=None, receipt_path=None, intent=None, package_fd=None):
        if receipt_path is not None:
            nh._save_new(
                receipt_path,
                {
                    "state": "sender_reaped",
                    "intent": intent,
                    "returncode": 1 if self.failure else 0,
                },
            )
        self.calls.append(("start", unit))
        if self.failure:
            raise subprocess.TimeoutExpired("systemctl", 120)
        self.active = "active"


@pytest.fixture
def legacy_start_recovery_mechanics(monkeypatch):
    """Test historical inner checks only; production sender-tree gate stays closed."""
    monkeypatch.setattr(nh, "_require_proven_start_sender_tree", lambda: None)


@pytest.fixture
def helper(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    registry.mkdir()
    root = tmp_path / "instance"
    (root / "config").mkdir(parents=True)
    (root / ".installation.lock").touch()
    for relative, data in [
        ("run", "#!/opt/python/bin/python3.13\n"),
        ("instance.json", '{"instance_id":"probe"}'),
        ("config/roles.json", '{"client":{}}'),
    ]:
        (root / relative).write_text(data)
    unit = tmp_path / "ai-device-client-probe.service"
    unit.write_text("fixture unit\n")
    environment = tmp_path / "token.env"
    environment.write_text("PRIVATE=never-return-this\n")
    (root / nh.MARKER).write_text("fixture management binding")
    record = {
        "management_sha256": nh._digest(root / nh.MARKER),
        "schema_version": 1,
        "instance_id": "probe",
        "root": str(root),
        "uid": os.getuid() or 12345,
        "gid": os.getgid(),
        "unit": unit.name,
        "unit_path": str(unit),
        "unit_sha256": nh._digest(unit),
        "runner_sha256": nh._digest(root / "run"),
        "identity_sha256": nh._digest(root / "instance.json"),
        "roles_sha256": nh._digest(root / "config/roles.json"),
        "environment_file": str(environment),
    }
    # Tests use owned temporary directories; production trust checks tested separately.
    monkeypatch.setattr(nh, "_trusted", lambda *a, **k: None)
    (registry / "probe.json").write_text(json.dumps(record))
    adapter = FakeSystemd(record)
    return nh.Helper(registry, adapter=adapter), adapter, record


def req(action="query", op="one"):
    return {"action": action, "instance_id": "probe", "operation_id": op}


def test_render_systemd_uses_nonroot_stable_runner(tmp_path):
    content = nh.render_client_unit(
        instance_id="probe",
        root=tmp_path,
        uid=12345,
        gid=12345,
        environment_file=tmp_path / "token.env",
    )
    assert "User=12345\n" in content
    assert f"ExecStart={tmp_path}/run client\n" in content
    assert "UMask=0077" in content
    assert "Restart=on-failure" in content
    assert "NoNewPrivileges=yes" in content


@pytest.mark.parametrize("path", ["relative", "/tmp/bad%value", "/tmp/space here", "/tmp/../etc"])
def test_unsafe_unit_paths_rejected(path):
    with pytest.raises(nh.HelperError):
        nh.render_client_unit(
            instance_id="probe", root=path, uid=12345, gid=1, environment_file="/etc/fixture.env"
        )


def test_root_unit_rejected(tmp_path):
    with pytest.raises(nh.HelperError, match="non-root"):
        nh.render_client_unit(
            instance_id="probe",
            root=tmp_path,
            uid=0,
            gid=0,
            environment_file=tmp_path / "token.env",
        )


def test_query_and_reconcile_never_start(helper):
    service, adapter, record = helper
    for action in ["query", "reconcile"]:
        result = service.handle(req(action), peer_uid=record["uid"])
        assert result["intent"] is None
        assert "never-return-this" not in json.dumps(result)
    assert not any(action == "start" for action, _ in adapter.calls)


@pytest.mark.parametrize("action", ["query", "reconcile"])
def test_read_rejects_operation_filename_identity_collision(helper, action):
    service, adapter, record = helper
    # probe + one-two and probe-one + two share the legacy filename.
    (service.registry / "op-probe-one-two.json").write_text(
        json.dumps({"action": "start", "instance_id": "probe-one", "operation_id": "two"})
    )
    (service.registry / "result-probe-one-two.json").write_text('{"state":"started"}')
    request = {"action": action, "instance_id": "probe", "operation_id": "one-two"}
    with pytest.raises(nh.HelperError, match="journal identity differs"):
        service.handle(request, peer_uid=record["uid"])
    assert not any(name == "start" for name, _ in adapter.calls)


@pytest.mark.parametrize("suffix", ["result", "recovery"])
def test_read_rejects_orphan_operation_record(helper, suffix):
    service, _, record = helper
    (service.registry / f"{suffix}-probe-one.json").write_text("{}")
    with pytest.raises(nh.HelperError, match="intent missing"):
        service.handle(req("reconcile"), peer_uid=record["uid"])


def test_read_rejects_recovery_bound_to_different_intent(helper):
    service, _, record = helper
    intent = {"action": "start", "instance_id": "probe", "operation_id": "one"}
    (service.registry / "op-probe-one.json").write_text(json.dumps(intent))
    (service.registry / "recovery-probe-one.json").write_text('{"intent":{}}')
    with pytest.raises(nh.HelperError, match="invalid recovery evidence"):
        service.handle(req("reconcile"), peer_uid=record["uid"])


@pytest.mark.parametrize("action", ["resolve-running", "resolve-stopped", "start", "reconcile"])
@pytest.mark.parametrize("change", ["intent", "state", "replayed"])
@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_existing_recovery_must_remain_bound(helper, action, change):
    service, adapter, record = helper
    adapter.failure = True
    service.handle(req("start"), peer_uid=record["uid"])
    adapter.failure = False
    adapter.active = "active"
    service.handle(req("resolve-running"), peer_uid=record["uid"])
    path = service.registry / "recovery-probe-one.json"
    recovery = json.loads(path.read_text())
    if change == "intent":
        recovery["intent"]["operation_id"] = "other"
    elif change == "state":
        recovery["state"] = "unknown"
    else:
        recovery["start_replayed"] = True
    path.write_text(json.dumps(recovery))
    before = sum(name == "start" for name, _ in adapter.calls)
    with pytest.raises(nh.HelperError, match="invalid recovery evidence"):
        service.handle(req(action), peer_uid=record["uid"])
    assert sum(name == "start" for name, _ in adapter.calls) == before


@pytest.mark.parametrize("failure_stage", ["file", "directory"])
def test_start_intent_fsync_failure_never_calls_sender(helper, monkeypatch, failure_stage):
    service, adapter, record = helper
    original_save = nh._save_new
    original_fsync = os.fsync
    armed = False

    def inject_save(path, value, **kwargs):
        nonlocal armed
        if path.name != "op-probe-one.json":
            return original_save(path, value, **kwargs)
        armed = True
        try:
            return original_save(path, value, **kwargs)
        finally:
            armed = False

    def fail_fsync(fd):
        if armed and stat.S_ISDIR(os.fstat(fd).st_mode) == (failure_stage == "directory"):
            raise OSError("injected start intent fsync failure")
        return original_fsync(fd)

    monkeypatch.setattr(nh, "_save_new", inject_save)
    monkeypatch.setattr(nh.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="injected start intent fsync failure"):
        service.handle(req("start"), peer_uid=record["uid"])
    intent = service.registry / "op-probe-one.json"
    assert intent.exists() is (failure_stage == "directory")
    assert not any(action == "start" for action, _ in adapter.calls)
    assert not (service.registry / "sender-probe-one.json").exists()
    if intent.exists():
        result = service.handle(req("start"), peer_uid=record["uid"])
        assert result["result"]["state"] == "unknown" and result["replayed"] is False
        with pytest.raises(nh.HelperError, match="unconfirmed operation"):
            service.handle(req("start", "two"), peer_uid=record["uid"])
        assert not any(action == "start" for action, _ in adapter.calls)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_start_process_exit_after_durable_intent_does_not_replay(helper, monkeypatch):
    if os.environ.get("DEVICE_CRASH_TEST_CHILD") != "1":
        env = {**os.environ, "DEVICE_CRASH_TEST_CHILD": "1"}
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"{__file__}::test_start_process_exit_after_durable_intent_does_not_replay"],
            check=True, timeout=30, env=env,
        )
        return
    service, adapter, record = helper
    parent_pid = os.getpid()
    original_save = nh._save_new
    sender_marker = service.registry / "unexpected-sender"

    def save_and_exit(path, value, **kwargs):
        original_save(path, value, **kwargs)
        if os.getpid() != parent_pid and path.name == "op-probe-one.json":
            os._exit(73)

    def unexpected_sender(*args, **kwargs):
        sender_marker.write_text("called")
        raise AssertionError("sender must not execute")

    monkeypatch.setattr(nh, "_save_new", save_and_exit)
    monkeypatch.setattr(adapter, "start", unexpected_sender)
    child = os.fork()
    if child == 0:
        try:
            service.handle(req("start"), peer_uid=record["uid"])
        finally:
            os._exit(74)
    _, status = os.waitpid(child, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 73
    assert (service.registry / "op-probe-one.json").exists()
    assert not sender_marker.exists()
    result = service.handle(req("start"), peer_uid=record["uid"])
    assert result["result"]["state"] == "unknown" and result["replayed"] is False
    with pytest.raises(nh.HelperError, match="unconfirmed operation"):
        service.handle(req("start", "two"), peer_uid=record["uid"])
    assert not sender_marker.exists()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_start_process_exit_after_sender_begins_stays_unknown(helper, monkeypatch):
    if os.environ.get("DEVICE_CRASH_TEST_CHILD") != "1":
        env = {**os.environ, "DEVICE_CRASH_TEST_CHILD": "1"}
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"{__file__}::test_start_process_exit_after_sender_begins_stays_unknown"],
            check=True, timeout=30, env=env,
        )
        return
    service, adapter, record = helper
    marker = service.registry / "sender-started-test-marker"
    parent_pid = os.getpid()

    def start_then_exit(*args, **kwargs):
        if os.getpid() == parent_pid:
            raise AssertionError("unknown operation must not replay")
        marker.write_text("started")
        os._exit(73)

    monkeypatch.setattr(adapter, "start", start_then_exit)
    child = os.fork()
    if child == 0:
        try:
            service.handle(req("start"), peer_uid=record["uid"])
        finally:
            os._exit(74)
    _, status = os.waitpid(child, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 73
    assert marker.read_text() == "started"
    assert (service.registry / "op-probe-one.json").exists()
    assert not (service.registry / "result-probe-one.json").exists()
    assert not (service.registry / "sender-probe-one.json").exists()
    result = service.handle(req("start"), peer_uid=record["uid"])
    assert result["result"]["state"] == "unknown" and result["replayed"] is False
    with pytest.raises(nh.HelperError, match="unconfirmed operation"):
        service.handle(req("start", "two"), peer_uid=record["uid"])
    assert marker.read_text() == "started"


def test_start_idempotency(helper):
    service, adapter, record = helper
    result = service.handle(req("start"), peer_uid=record["uid"])
    assert result["result"]["state"] == "started"
    second = service.handle(req("start"), peer_uid=record["uid"])
    assert second["result"]["state"] == "started"
    assert sum(action == "start" for action, _ in adapter.calls) == 1


def test_unknown_start_is_not_replayed(helper):
    service, adapter, record = helper
    adapter.failure = True
    assert service.handle(req("start"), peer_uid=record["uid"])["result"]["state"] == "unknown"
    adapter.failure = False
    assert service.handle(req("start"), peer_uid=record["uid"])["result"]["state"] == "unknown"
    assert sum(action == "start" for action, _ in adapter.calls) == 1


@pytest.mark.parametrize("change", ["argv", "action", "instance_id", "operation_id"])
def test_reject_unbounded_inputs(helper, change):
    service, adapter, record = helper
    request = req()
    request[change] = "../../etc" if change != "action" else "stop"
    with pytest.raises(nh.HelperError):
        service.handle(request, peer_uid=record["uid"])
    assert not adapter.calls


def test_peer_identity_not_self_reported(helper):
    service, adapter, _ = helper
    with pytest.raises(nh.HelperError, match="peer"):
        service.handle(req("start"), peer_uid=999999)
    assert not adapter.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("User", "0"),
        ("Group", "0"),
        ("FragmentPath", "/etc/other.service"),
        ("ExecStart", "different argv"),
    ],
)
def test_loaded_binding_must_match(helper, field, value):
    service, adapter, record = helper
    adapter.override[field] = value
    with pytest.raises(nh.HelperError, match="loaded systemd"):
        service.handle(req("start"), peer_uid=record["uid"])
    assert not any(action == "start" for action, _ in adapter.calls)


def test_changed_runner_refuses_before_systemd(helper):
    service, adapter, record = helper
    Path(record["root"], "run").write_text("changed")
    with pytest.raises(nh.HelperError, match="binding changed"):
        service.handle(req("start"), peer_uid=record["uid"])
    assert not adapter.calls


def test_management_lock_contention(helper):
    service, adapter, record = helper
    with (service.registry / "probe.lock").open("a+b") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(nh.HelperError, match="busy"):
            service.handle(req("start"), peer_uid=record["uid"])
    assert not adapter.calls


def test_intent_without_result_never_replays(helper):
    service, adapter, record = helper
    intent = {
        "action": "start",
        "instance_id": "probe",
        "operation_id": "one",
        "unit_sha256": record["unit_sha256"],
    }
    nh._save_new(service.registry / "op-probe-one.json", intent)
    assert service.handle(req("start"), peer_uid=record["uid"])["result"]["state"] == "unknown"
    assert not any(action == "start" for action, _ in adapter.calls)


@pytest.mark.parametrize("result", [{}, {"state": "failed"}, {"state": []}, []])
def test_invalid_prior_result_blocks_new_start(helper, result):
    service, adapter, record = helper
    service.handle(req("start"), peer_uid=record["uid"])
    (service.registry / "result-probe-one.json").write_text(json.dumps(result))
    before = len([call for call in adapter.calls if call[0] == "start"])
    with pytest.raises(nh.HelperError, match="invalid start result"):
        service.handle(req("start", "next"), peer_uid=record["uid"])
    assert len([call for call in adapter.calls if call[0] == "start"]) == before
    assert not (service.registry / "op-probe-next.json").exists()


def test_atomic_save_never_overwrites(tmp_path):
    path = tmp_path / "record.json"
    nh._save_new(path, {"keep": True})
    with pytest.raises(FileExistsError):
        nh._save_new(path, {"keep": False})
    assert json.loads(path.read_text()) == {"keep": True}
    assert not list(tmp_path.glob(".helper-*"))


def test_untrusted_path_refused(tmp_path):
    path = tmp_path / "record"
    path.write_text("fixture")
    if os.getuid() != 0:
        with pytest.raises(nh.HelperError, match="administrator"):
            nh._trusted(path)
    else:
        path.chmod(0o666)
        with pytest.raises(nh.HelperError, match="administrator"):
            nh._trusted(path)


def test_unknown_blocks_new_operation_id(helper):
    service, adapter, record = helper
    adapter.failure = True
    service.handle(req("start"), peer_uid=record["uid"])
    adapter.failure = False
    with pytest.raises(nh.HelperError, match="unconfirmed"):
        service.handle(req("start", op="different"), peer_uid=record["uid"])
    assert sum(action == "start" for action, _ in adapter.calls) == 1


def test_non_string_action_rejected(helper):
    service, _, record = helper
    request = req()
    request["action"] = []
    with pytest.raises(nh.HelperError, match="unsupported"):
        service.handle(request, peer_uid=record["uid"])


def test_installation_lock_blocks_start(helper):
    service, adapter, record = helper
    with Path(record["root"], ".installation.lock").open("rb") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = service.handle(req("start"), peer_uid=record["uid"])
    assert result["result"]["state"] == "unknown"
    assert not any(action == "start" for action, _ in adapter.calls)


def test_nonterminal_upgrade_blocks_start(helper):
    service, adapter, record = helper
    upgrades = Path(record["root"], "upgrades")
    upgrades.mkdir()
    (upgrades / ".supervisor.lock").touch()
    (upgrades / "previous.json").write_text('{"state":"unknown"}')
    result = service.handle(req("start"), peer_uid=record["uid"])
    assert result["result"]["state"] == "unknown"
    assert not any(action == "start" for action, _ in adapter.calls)


def test_render_helper_uses_independent_bootstrap(tmp_path):
    result = nh.render_helper_unit(
        bootstrap_python=tmp_path / "python",
        registry=tmp_path,
        socket_path=tmp_path / "helper.sock",
        group=12345,
    )
    assert "User=root" in result
    assert "Restart=no" in result
    assert "ExecStartPre=" not in result
    assert f"ExecStart={tmp_path / 'python'} -I -B -m device_runtime.native_helper serve " in result


def test_peer_process_rejects_non_root_and_unsupported_kernel(monkeypatch):
    import errno
    import struct
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(nh.socket, "SO_PEERCRED", 17, raising=False)

    class Peer:
        def __init__(self, uid):
            self.uid = uid

        def getsockopt(self, level, option, size):
            if option == nh.socket.SO_PEERCRED:
                return struct.pack("3i", 42, self.uid, 0)
            raise OSError(errno.ENOPROTOOPT, "unsupported")

    with (
        pytest.raises(nh.HelperError, match="unexpected helper peer credentials"),
        nh._socket_peer_process(Peer(1000)),
    ):
        pytest.fail("untrusted peer accepted")
    with (
        pytest.raises(nh.HelperError, match="cannot establish helper peer process identity"),
        nh._socket_peer_process(Peer(0)),
    ):
        pytest.fail("unsupported kernel accepted")


@pytest.mark.parametrize("scenario", ["mismatch", "exited_before", "exited_after", "caller_error", "success"])
def test_peer_process_closes_pidfd_on_each_observation_path(monkeypatch, scenario):
    import builtins
    import errno
    import io
    import select
    import struct

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(nh.socket, "SO_PEERCRED", 17, raising=False)
    pidfd, write_fd = os.pipe()
    real_open = builtins.open

    class Peer:
        def getsockopt(self, level, option, size):
            if option == nh.socket.SO_PEERCRED:
                return struct.pack("3i", 42, 0, 0)
            return struct.pack("i", pidfd)

    def fake_open(path, *args, **kwargs):
        if path == f"/proc/self/fdinfo/{pidfd}":
            return io.StringIO(f"Pid:\t{43 if scenario == 'mismatch' else 42}\n")
        return real_open(path, *args, **kwargs)

    class Poller:
        def __init__(self):
            self.calls = 0

        def register(self, fd, events):
            assert fd == pidfd
            assert events == select.POLLIN

        def poll(self, timeout):
            assert timeout == 0
            self.calls += 1
            if scenario == "exited_before" or (scenario == "exited_after" and self.calls == 2):
                return [(pidfd, select.POLLIN)]
            return []

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(select, "poll", Poller)
    try:
        if scenario in {"mismatch", "exited_before", "exited_after"}:
            with pytest.raises(nh.HelperError), nh._socket_peer_process(Peer()) as peer:
                assert scenario == "exited_after"
                assert peer["pid"] == 42
        elif scenario == "caller_error":
            with pytest.raises(RuntimeError, match="caller failed"), nh._socket_peer_process(Peer()):
                raise RuntimeError("caller failed")
        else:
            with nh._socket_peer_process(Peer()) as peer:
                assert peer["pid"] == 42 and peer["pidfd"] == pidfd
                assert os.get_inheritable(pidfd) is False
        with pytest.raises(OSError) as exc:
            os.fstat(pidfd)
        assert exc.value.errno == errno.EBADF
    finally:
        os.close(write_fd)
        try:
            os.close(pidfd)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise


@pytest.mark.parametrize("failure", [RuntimeError("init failed"), KeyboardInterrupt()])
def test_socket_peer_pidfd_closed_when_initialization_interrupted(monkeypatch, failure):
    import errno
    import socket
    import struct

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    pidfd, write_fd = os.pipe()

    class Peer:
        def getsockopt(self, level, option, size):
            if option == socket.SO_PEERCRED:
                return struct.pack("3i", 42, 0, 0)
            return struct.pack("i", pidfd)

    def fail_initialization(fd, inheritable):
        assert fd == pidfd and inheritable is False
        raise failure

    monkeypatch.setattr(nh.os, "set_inheritable", fail_initialization)
    try:
        with pytest.raises(type(failure)), nh._socket_peer_process(Peer()):
            pytest.fail("initialization must not succeed")
        with pytest.raises(OSError) as exc:
            os.fstat(pidfd)
        assert exc.value.errno == errno.EBADF
    finally:
        os.close(write_fd)
        try:
            os.close(pidfd)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise


def test_helper_unit_peer_observation_refuses_drift_and_pid_mismatch(monkeypatch):
    import sys
    from contextlib import contextmanager
    from types import SimpleNamespace

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)

    @contextmanager
    def peer(_connection):
        yield {"pid": 123, "uid": 0, "gid": 0, "pidfd": 7}

    monkeypatch.setattr(nh, "_socket_peer_process", peer)
    fields = {
        "LoadState": "loaded", "ActiveState": "active", "SubState": "running",
        "MainPID": "123", "InvocationID": "a" * 32,
        "FragmentPath": "/etc/systemd/system/ai-device-native-helper.service",
    }

    def output(values):
        return SimpleNamespace(stdout="".join(f"{key}={value}\n" for key, value in values.items()))

    def run_with(*observations):
        remaining = iter(observations)
        return lambda *args, **kwargs: output(next(remaining))

    result = nh.observe_helper_unit_peer(None, runner=run_with(fields, fields))
    assert result["pid"] == 123 and result["dispatch_allowed"] is False
    assert result["loaded_code_verified"] is False
    changed = {**fields, "InvocationID": "b" * 32}
    with pytest.raises(nh.HelperError, match="differ or changed"):
        nh.observe_helper_unit_peer(None, runner=run_with(fields, changed))
    mismatch = {**fields, "MainPID": "124"}
    with pytest.raises(nh.HelperError, match="differ or changed"):
        nh.observe_helper_unit_peer(None, runner=run_with(mismatch, mismatch))
    with pytest.raises(nh.HelperError, match="not stably running"):
        nh.observe_helper_unit_peer(None, runner=run_with({**fields, "ActiveState": "inactive"}))
    with pytest.raises(nh.HelperError, match="incomplete"):
        nh.observe_helper_unit_peer(None, runner=run_with({"MainPID": "123"}))

    events = []

    @contextmanager
    def tracked_peer(_connection):
        events.append("opened")
        try:
            yield {"pid": 123, "uid": 0, "gid": 0, "pidfd": 7}
        finally:
            events.append("closed")

    monkeypatch.setattr(nh, "_socket_peer_process", tracked_peer)
    with (
        pytest.raises(nh.HelperError, match="differ or changed"),
        nh.scoped_helper_unit_peer(None, runner=run_with(fields, changed)) as scoped,
    ):
        assert scoped["dispatch_allowed"] is False
        assert events == ["opened"]
    assert events == ["opened", "closed"]
    events.clear()
    with (
        pytest.raises(RuntimeError, match="caller failed"),
        nh.scoped_helper_unit_peer(None, runner=run_with(fields)),
    ):
        raise RuntimeError("caller failed")
    assert events == ["opened", "closed"]


def test_call_helper_does_not_retry(tmp_path):
    import socket
    import threading

    path = tmp_path / "helper.sock"
    received = []
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        server.listen(1)

        def reply():
            connection, _ = server.accept()
            with connection:
                received.append(json.loads(connection.recv(4096)))
                connection.sendall(b'{"ok":true,"result":{"state":"fixture"}}\n')

        thread = threading.Thread(target=reply)
        thread.start()
        result = nh.call_helper(path, req())
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert result["ok"] is True and received == [req()]


def test_call_helper_lost_response_reports_unknown(tmp_path):
    import socket
    import threading

    path = tmp_path / "helper.sock"
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        server.listen(1)

        def drop():
            connection, _ = server.accept()
            with connection:
                connection.recv(4096)

        thread = threading.Thread(target=drop)
        thread.start()
        with pytest.raises(nh.HelperError, match="same operation"):
            nh.call_helper(path, req("start"))
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(nh.sys, "platform", "linux")
    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)
    monkeypatch.setattr(nh, "_trusted", lambda *a, **kw: None)
    monkeypatch.setattr(nh, "RUNTIME_ROOT", tmp_path)
    python = tmp_path / "python"
    python.write_text("fixture")
    python.chmod(0o755)
    units = tmp_path / "units"
    units.mkdir()
    return {
        "bootstrap_python": python,
        "registry": tmp_path / "registry",
        "unit_dir": units,
        "runtime_dir": tmp_path / "runtime",
        "group": 12345,
    }


def test_deploy_helper_no_start_and_no_replace(deployment):
    result = nh.deploy_helper(**deployment)
    assert result["started"] is False and result["enabled"] is False
    unit = Path(result["unit"])
    content = unit.read_text()
    assert "RuntimeDirectory=runtime\n" in content
    assert "RuntimeDirectoryPreserve=yes" in content
    assert "KillMode=process\n" in content
    assert "Restart=no\n" in content
    assert "ExecStartPre" in content and "ExecStartPre=" not in content
    assert "--group 12345" in content
    with pytest.raises(nh.HelperError, match="already exists"):
        nh.deploy_helper(**deployment)
    assert unit.read_text() == content


def test_deploy_candidate_unit_requires_fixed_bootstrap_and_verified_candidate(deployment, monkeypatch):
    from device_runtime import native_bootstrap as bootstrap

    fixed = deployment["bootstrap_python"].parent / "bootstrap"
    python = fixed / "bin/python3.13"
    python.parent.mkdir(parents=True)
    python.write_text("fixture")
    python.chmod(0o755)
    monkeypatch.setattr(bootstrap, "TRUSTED_HELPER_BOOTSTRAP", fixed)
    registry = deployment["registry"]
    registry.mkdir()
    (registry / "package-manager.execution.lock").touch(mode=0o600)
    observed = []
    def read_candidate(path, version):
        observed.append((path, version))
        return {"snapshot_sha256": "a" * 64}

    monkeypatch.setattr(bootstrap, "read_helper_candidate", read_candidate)
    options = {**deployment, "bootstrap_python": python, "candidate_version": "release-1"}
    result = nh.deploy_helper(**options)
    unit = Path(result["unit"])
    content = unit.read_text()
    assert observed == [(registry / "bootstrap-candidates", "release-1")]
    assert f"{python} -I -B -m device_runtime.native_bootstrap" in content
    assert "--version release-1" in content and "--snapshot-sha256 " + "a" * 64 in content
    assert "KillMode=process" in content
    assert result["started"] is False and result["enabled"] is False
    with pytest.raises(nh.HelperError, match="already exists"):
        nh.deploy_helper(**options)
    assert unit.read_text() == content


@pytest.mark.parametrize("reason", ["wrong_python", "invalid_version", "candidate_invalid", "lock_busy"])
def test_deploy_candidate_refuses_before_unit_write(deployment, monkeypatch, reason):
    from device_runtime import native_bootstrap as bootstrap

    fixed = deployment["bootstrap_python"].parent / "bootstrap"
    python = fixed / "bin/python3.13"
    python.parent.mkdir(parents=True)
    python.write_text("fixture")
    python.chmod(0o755)
    monkeypatch.setattr(bootstrap, "TRUSTED_HELPER_BOOTSTRAP", fixed)
    registry = deployment["registry"]
    registry.mkdir()
    (registry / "package-manager.execution.lock").touch(mode=0o600)
    options = {**deployment, "bootstrap_python": python, "candidate_version": "release-1"}
    if reason == "wrong_python":
        options["bootstrap_python"] = deployment["bootstrap_python"]
    if reason == "invalid_version":
        options["candidate_version"] = "../escape"
    if reason == "candidate_invalid":
        def reject(*_args):
            raise bootstrap.BootstrapError("candidate invalid")
        monkeypatch.setattr(bootstrap, "read_helper_candidate", reject)
    if reason == "lock_busy":
        with nh.package_lease(registry), pytest.raises(nh.HelperError, match="still executing"):
            nh.deploy_helper(**options)
    else:
        with pytest.raises((nh.HelperError, bootstrap.BootstrapError)):
            nh.deploy_helper(**options)
    assert not (deployment["unit_dir"] / "ai-device-native-helper.service").exists()
    assert not deployment["runtime_dir"].exists()


def test_deploy_helper_requires_administrator(deployment, monkeypatch):
    monkeypatch.setattr(nh.os, "geteuid", lambda: 12345)
    with pytest.raises(nh.HelperError, match="administrator"):
        nh.deploy_helper(**deployment)
    assert not deployment["registry"].exists()


def test_deploy_helper_bad_runtime_has_no_side_effect(deployment):
    deployment["runtime_dir"] /= "nested"
    with pytest.raises(nh.HelperError, match="directly beneath"):
        nh.deploy_helper(**deployment)
    assert not deployment["registry"].exists()


def test_deploy_helper_rejects_untrusted_bootstrap(deployment, monkeypatch):
    def reject(*a, **kw):
        raise nh.HelperError("untrusted bootstrap")

    monkeypatch.setattr(nh, "_trusted", reject)
    with pytest.raises(nh.HelperError, match="untrusted"):
        nh.deploy_helper(**deployment)
    assert not deployment["registry"].exists()


@pytest.mark.parametrize("ok,code", [(True, None), (False, 1)])
def test_call_cli_failure_exit(monkeypatch, capsys, ok, code):
    monkeypatch.setattr(
        nh.sys,
        "argv",
        [
            "helper",
            "call",
            "--socket",
            "/run/probe.sock",
            "--action",
            "query",
            "--instance-id",
            "probe",
            "--operation-id",
            "query-1",
        ],
    )
    monkeypatch.setattr(nh, "call_helper", lambda *a: {"ok": ok})
    if code is not None:
        with pytest.raises(SystemExit) as result:
            nh.main()
        assert result.value.code == code
    else:
        nh.main()
    assert json.loads(capsys.readouterr().out) == {"ok": ok}


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(nh.sys, "platform", "linux")
    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)
    monkeypatch.setattr(nh, "_trusted", lambda *a, **kw: None)
    registry = tmp_path / "registry"
    registry.mkdir()
    return registry, tmp_path / "helper.sock"


def test_recover_socket_absent_and_file_refused(recovery):
    registry, path = recovery
    assert nh.recover_socket(registry, path)["removed"] is False
    path.write_text("not a socket")
    with pytest.raises(nh.HelperError, match="root-owned Unix socket"):
        nh.recover_socket(registry, path)
    assert path.read_text() == "not a socket"


def test_recover_socket_daemon_lock_blocks(recovery):
    registry, path = recovery
    with nh._daemon_lease(registry), pytest.raises(nh.HelperError, match="already active"):
        nh.recover_socket(registry, path)


def test_daemon_lease_exposes_owned_descriptor_and_closes_it(recovery):
    registry, _ = recovery
    with nh._daemon_lease(registry) as fd:
        assert isinstance(fd, int)
        assert os.fstat(fd).st_ino == (registry / "helper-daemon.lock").stat().st_ino
        with pytest.raises(nh.HelperError, match="already active"), nh._daemon_lease(registry):
            pass
    with pytest.raises(OSError):
        os.fstat(fd)
    with nh._daemon_lease(registry):
        pass


def test_daemon_lease_rejects_replaced_path_during_lock(recovery, monkeypatch):
    registry, _ = recovery
    lock_path = registry / "helper-daemon.lock"
    moved = registry / "retained-original.lock"
    original_flock = nh.fcntl.flock

    def replace_after_lock(fd, operation):
        original_flock(fd, operation)
        if stat.S_ISREG(os.fstat(fd).st_mode):
            lock_path.rename(moved)
            lock_path.write_bytes(b"")
            lock_path.chmod(0o600)

    monkeypatch.setattr(nh.fcntl, "flock", replace_after_lock)
    with pytest.raises(nh.HelperError, match="path changed during acquisition"), nh._daemon_lease(registry):
        pytest.fail("drifted lease entered")
    assert moved.is_file() and lock_path.is_file()


def test_daemon_lease_rejects_non_regular_lock(recovery):
    registry, _ = recovery
    (registry / "helper-daemon.lock").mkdir()
    with pytest.raises(OSError), nh._daemon_lease(registry):
        pytest.fail("directory lease entered")


def test_daemon_anchor_blocks_replaced_lock_path(recovery):
    registry, _ = recovery
    lock_path = registry / "helper-daemon.lock"
    with nh._daemon_lease(registry):
        lock_path.rename(registry / "retained-old.lock")
        lock_path.write_bytes(b"")
        lock_path.chmod(0o600)
        with pytest.raises(nh.HelperError, match="already active"), nh._daemon_lease(registry):
            pytest.fail("directory anchor failed to serialize")


def test_daemon_anchor_rejects_directory_drift_during_lock(recovery, monkeypatch):
    registry, _ = recovery
    original_flock = nh.fcntl.flock

    def replace_after_lock(fd, operation):
        original_flock(fd, operation)
        registry.rename(registry.parent / "retained-registry")
        registry.mkdir()

    monkeypatch.setattr(nh.fcntl, "flock", replace_after_lock)
    with pytest.raises(nh.HelperError, match="anchor changed during acquisition"), nh._daemon_lease(registry):
        pytest.fail("drifted directory anchor entered")


def test_daemon_anchor_retains_old_file_lock_conflict(recovery):
    registry, _ = recovery
    with (
        nh._daemon_file_lease(registry),
        pytest.raises(nh.HelperError, match="already active"),
        nh._daemon_lease(registry),
    ):
        pytest.fail("old file lock conflict ignored")
    with nh._daemon_lease(registry):
        pass


def test_recover_socket_requires_administrator(recovery, monkeypatch):
    monkeypatch.setattr(nh.os, "geteuid", lambda: 12345)
    with pytest.raises(nh.HelperError, match="administrator"):
        nh.recover_socket(*recovery)


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_resolve_unknown_running_without_replay(helper):
    service, adapter, record = helper
    adapter.failure = True
    service.handle(req("start"), peer_uid=record["uid"])
    original = (service.registry / "result-probe-one.json").read_bytes()
    adapter.failure = False
    adapter.active = "active"
    recovered = service.handle(req("resolve-running"), peer_uid=record["uid"])
    assert recovered["recovery"]["state"] == "confirmed_running"
    assert recovered["recovery"]["start_replayed"] is False
    assert (service.registry / "result-probe-one.json").read_bytes() == original
    assert (
        service.handle(req("resolve-running"), peer_uid=record["uid"])["recovery"]
        == recovered["recovery"]
    )
    assert (
        service.handle(req("reconcile"), peer_uid=record["uid"])["recovery"]
        == recovered["recovery"]
    )
    service.handle(req("start", "next"), peer_uid=record["uid"])
    assert sum(a == "start" for a, _ in adapter.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("ActiveState", "inactive"),
        ("ActiveState", "failed"),
        ("SubState", "auto-restart"),
        ("MainPID", "0"),
        ("Job", "42 start"),
        ("Job", None),
    ],
)
@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_unknown_recovery_requires_running_evidence(helper, field, value):
    service, adapter, record = helper
    adapter.failure = True
    service.handle(req("start"), peer_uid=record["uid"])
    adapter.active = "active"
    adapter.override[field] = value
    with pytest.raises(nh.HelperError, match="remains unknown"):
        service.handle(req("resolve-running"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_recovery_missing_result_and_missing_intent(helper):
    service, adapter, record = helper
    with pytest.raises(nh.HelperError, match="intent"):
        service.handle(req("resolve-running"), peer_uid=record["uid"])
    adapter.failure = True
    service.handle(req("start"), peer_uid=record["uid"])
    (service.registry / "result-probe-one.json").unlink()
    adapter.active = "active"
    service.handle(req("resolve-running"), peer_uid=record["uid"])
    assert not (service.registry / "result-probe-one.json").exists()


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_recovery_persistence_failure_still_blocks_new_start(helper, monkeypatch):
    service, adapter, record = helper
    adapter.failure = True
    service.handle(req("start"), peer_uid=record["uid"])
    adapter.active = "active"
    original_save = nh._save_new

    def fail_save(path, value):
        if path.name.startswith("recovery-"):
            raise OSError("injected storage failure")
        original_save(path, value)

    monkeypatch.setattr(nh, "_save_new", fail_save)
    with pytest.raises(OSError, match="storage failure"):
        service.handle(req("resolve-running"), peer_uid=record["uid"])
    with pytest.raises(nh.HelperError, match="unconfirmed"):
        service.handle(req("start", "next"), peer_uid=record["uid"])
    assert sum(a == "start" for a, _ in adapter.calls) == 1


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_recovery_maintenance_lock_blocks(helper):
    service, adapter, record = helper
    adapter.failure = True
    service.handle(req("start"), peer_uid=record["uid"])
    adapter.active = "active"
    with (Path(record["root"]) / ".installation.lock").open("rb") as lease:
        nh.fcntl.flock(lease, nh.fcntl.LOCK_EX | nh.fcntl.LOCK_NB)
        with pytest.raises(nh.HelperError, match="installation"):
            service.handle(req("resolve-running"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_completed_start_cannot_be_reclassified(helper):
    service, _adapter, record = helper
    service.handle(req("start"), peer_uid=record["uid"])
    with pytest.raises(nh.HelperError, match="only an unknown"):
        service.handle(req("resolve-running"), peer_uid=record["uid"])


def test_recovery_authorization_and_binding(helper):
    service, adapter, record = helper
    adapter.failure = True
    service.handle(req("start"), peer_uid=record["uid"])
    adapter.active = "active"
    with pytest.raises(nh.HelperError, match="peer"):
        service.handle(req("resolve-running"), peer_uid=999999)
    adapter.override["User"] = "0"
    with pytest.raises(nh.HelperError, match="loaded systemd"):
        service.handle(req("resolve-running"), peer_uid=record["uid"])


def stopped_unknown(helper):
    service, adapter, record = helper
    adapter.failure = True
    service.handle(req("start"), peer_uid=record["uid"])
    adapter.failure = False
    adapter.override.update(SubState="dead", MainPID="0")
    return service, adapter, record


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_resolve_stopped_then_explicit_new_start(helper):
    service, adapter, record = stopped_unknown(helper)
    original = (service.registry / "result-probe-one.json").read_bytes()
    recovery = service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    assert recovery["recovery"]["state"] == "confirmed_stopped"
    assert (service.registry / "result-probe-one.json").read_bytes() == original
    assert sum(a == "start" for a, _ in adapter.calls) == 1
    assert service.handle(req("start"), peer_uid=record["uid"])["result"]["state"] == "unknown"
    adapter.override.clear()
    assert (
        service.handle(req("start", "explicit-retry"), peer_uid=record["uid"])["result"]["state"]
        == "started"
    )
    service.handle(req("start", "explicit-retry"), peer_uid=record["uid"])
    assert sum(a == "start" for a, _ in adapter.calls) == 2


def test_stopped_recovery_sender_lock_blocks(helper):
    service, _adapter, record = stopped_unknown(helper)
    with service._execution_lease("probe"), pytest.raises(nh.HelperError, match="sender"):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()


def test_stopped_recovery_legacy_refused(helper):
    service, _adapter, record = stopped_unknown(helper)
    path = service.registry / "op-probe-one.json"
    intent = json.loads(path.read_text())
    intent.pop("execution_barrier")
    path.write_text(json.dumps(intent))
    with pytest.raises(nh.HelperError, match="legacy"):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])


@pytest.mark.parametrize(
    "field,value",
    [("ActiveState", "active"), ("MainPID", "123"), ("SubState", "auto-restart"), ("Job", None)],
)
def test_stopped_recovery_uncertain_refused(helper, field, value):
    service, adapter, record = stopped_unknown(helper)
    adapter.override[field] = value
    with pytest.raises(nh.HelperError):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_stopped_recovery_cancel_known_job_and_recheck(helper):
    service, adapter, record = stopped_unknown(helper)
    adapter.override["Job"] = "42 start"
    cancelled = []

    def cancel(job):
        cancelled.append(job)
        adapter.override["Job"] = ""

    adapter.cancel_job = cancel
    result = service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    assert cancelled == ["42"]
    assert result["recovery"]["state"] == "confirmed_stopped"


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_stopped_recovery_cancel_race_refuses(helper):
    service, adapter, record = stopped_unknown(helper)
    adapter.override["Job"] = "42 start"

    def cancel(_job):
        adapter.override.update(Job="", ActiveState="active", MainPID="123", SubState="running")

    adapter.cancel_job = cancel
    with pytest.raises(nh.HelperError, match="not quiescent"):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()


def test_systemd_guard_inherits_sender_lock(tmp_path):
    import threading
    import time

    executable = tmp_path / "systemctl-fixture"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    executable.write_text(
        f"#!/bin/sh\ntouch '{ready}'\nwhile [ ! -e '{release}' ]; do sleep 0.02; done\n"
    )
    executable.chmod(0o755)
    lock = tmp_path / "execution.lock"
    errors = []
    with lock.open("a+b") as lease:
        nh.fcntl.flock(lease, nh.fcntl.LOCK_EX)

        def run():
            try:
                nh.Systemd(str(executable)).start(
                    "fixture.service",
                    lease_fd=lease.fileno(),
                    receipt_path=tmp_path / "receipt.json",
                    intent={"fixture": True},
                )
            except (OSError, subprocess.SubprocessError, nh.HelperError) as exc:
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            for _ in range(100):
                if ready.exists():
                    break
                time.sleep(0.02)
            assert ready.exists()
            # Closing the parent descriptor models the daemon losing ownership.
            lease.close()
            with lock.open("rb") as probe, pytest.raises(BlockingIOError):
                nh.fcntl.flock(probe, nh.fcntl.LOCK_EX | nh.fcntl.LOCK_NB)
        finally:
            release.touch()
            thread.join(timeout=5)
        assert not thread.is_alive() and not errors
    with lock.open("rb") as probe:
        nh.fcntl.flock(probe, nh.fcntl.LOCK_EX | nh.fcntl.LOCK_NB)


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_hung_guard_keeps_both_leases_and_unknown(helper, tmp_path):
    import threading
    import time

    service, _adapter, record = stopped_unknown(helper)
    receipt = service.registry / "sender-probe-one.json"
    receipt.unlink()
    executable = tmp_path / "held-systemctl"
    ready = tmp_path / "sender-ready"
    release = tmp_path / "sender-release"
    executable.write_text(
        f"#!/bin/sh\ntouch '{ready}'\nwhile [ ! -e '{release}' ]; do sleep 0.02; done\n"
    )
    executable.chmod(0o700)
    errors = []
    thread = None
    try:
        with (
            service._execution_lease("probe", existing=True) as execution_fd,
            nh.package_lease(service.registry) as package_fd,
        ):
            def run_guard():
                try:
                    nh.Systemd(str(executable)).start(
                        record["unit"], lease_fd=execution_fd, package_fd=package_fd,
                        receipt_path=tmp_path / "unrelated-receipt.json", intent={"fixture": True},
                    )
                except (OSError, subprocess.SubprocessError, nh.HelperError) as exc:
                    errors.append(exc)

            thread = threading.Thread(target=run_guard)
            thread.start()
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists()
        # Parent descriptors are closed; only the guard retains these leases.
        with pytest.raises(nh.HelperError, match="still active"):
            service.handle(req("resolve-stopped"), peer_uid=record["uid"])
        with pytest.raises(nh.HelperError, match="unconfirmed"):
            service.handle(req("start", "new-id"), peer_uid=record["uid"])
        with pytest.raises(nh.HelperError), nh.package_lease(service.registry, exclusive=True):
            pass
        assert not (service.registry / "recovery-probe-one.json").exists()
        assert not (service.registry / "op-probe-new-id.json").exists()
    finally:
        release.touch()
        if thread is not None:
            thread.join(timeout=5)
    assert thread is not None and not thread.is_alive() and not errors
    # Releasing locks or an unrelated receipt cannot establish recovery.
    with pytest.raises(nh.HelperError, match="receipt missing"):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_paused_guard_retains_leases_until_reaped_receipt(helper, tmp_path, monkeypatch):
    import signal
    import threading
    import time

    service, _adapter, record = stopped_unknown(helper)
    receipt = service.registry / "sender-probe-one.json"
    receipt.unlink()
    intent = json.loads((service.registry / "op-probe-one.json").read_text())
    executable = tmp_path / "controlled-sender"
    ready, release, done = (tmp_path / name for name in ("ready", "release", "done"))
    executable.write_text(
        f"#!/bin/sh\ntouch '{ready}'\nwhile [ ! -e '{release}' ]; do sleep 0.02; done\ntouch '{done}'\n"
    )
    executable.chmod(0o700)
    guard_started = threading.Event()
    guard = []
    errors = []

    def run_child(args, *, check, capture_output, pass_fds, env):
        child = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=pass_fds, env=env)
        guard.append(child)
        guard_started.set()
        stdout, stderr = child.communicate()
        if check and child.returncode:
            raise subprocess.CalledProcessError(child.returncode, args, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(args, child.returncode, stdout, stderr)

    monkeypatch.setattr(nh.subprocess, "run", run_child)
    thread = None
    paused = False
    try:
        with (
            service._execution_lease("probe", existing=True) as execution_fd,
            nh.package_lease(service.registry) as package_fd,
        ):
            def launch():
                try:
                    nh.Systemd(str(executable)).start(
                        record["unit"], lease_fd=execution_fd, package_fd=package_fd,
                        receipt_path=receipt, intent=intent,
                    )
                except (OSError, subprocess.SubprocessError, nh.HelperError) as exc:
                    errors.append(exc)

            thread = threading.Thread(target=launch)
            thread.start()
            assert guard_started.wait(5)
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists() and guard[0].poll() is None
            guard[0].send_signal(signal.SIGSTOP)
            paused = True
            pid, status = os.waitpid(guard[0].pid, os.WUNTRACED)
            assert pid == guard[0].pid and os.WIFSTOPPED(status)
        release.touch()
        deadline = time.monotonic() + 5
        while not done.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert done.exists() and guard[0].poll() is None
        assert not receipt.exists()
        with pytest.raises(nh.HelperError, match="still active"):
            service.handle(req("resolve-stopped"), peer_uid=record["uid"])
        with pytest.raises(nh.HelperError, match="unconfirmed"):
            service.handle(req("start", "new-id"), peer_uid=record["uid"])
        with pytest.raises(nh.HelperError), nh.package_lease(service.registry, exclusive=True):
            pass
        assert not (service.registry / "recovery-probe-one.json").exists()
    finally:
        release.touch()
        if paused and guard and guard[0].poll() is None:
            guard[0].send_signal(signal.SIGCONT)
        if thread is not None:
            thread.join(timeout=10)
    assert thread is not None and not thread.is_alive() and not errors
    assert json.loads(receipt.read_text())["intent"] == intent
    assert service.handle(req("resolve-stopped"), peer_uid=record["uid"])["recovery"]["state"] == "confirmed_stopped"


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_stopped_cancel_failure_keeps_unknown(helper):
    service, adapter, record = stopped_unknown(helper)
    adapter.override["Job"] = "42 start"

    def fail(_job):
        raise subprocess.TimeoutExpired("cancel", 30)

    adapter.cancel_job = fail
    with pytest.raises(subprocess.TimeoutExpired):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    with pytest.raises(nh.HelperError, match="unconfirmed"):
        service.handle(req("start", "next"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_stopped_recovery_storage_failure_keeps_unknown(helper, monkeypatch):
    service, _adapter, record = stopped_unknown(helper)
    original = nh._save_new

    def fail(path, value):
        if path.name.startswith("recovery-"):
            raise OSError("storage failed")
        original(path, value)

    monkeypatch.setattr(nh, "_save_new", fail)
    with pytest.raises(OSError, match="storage failed"):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    with pytest.raises(nh.HelperError, match="unconfirmed"):
        service.handle(req("start", "next"), peer_uid=record["uid"])


def test_running_recovery_also_requires_sender_exit(helper):
    service, adapter, record = stopped_unknown(helper)
    adapter.override.clear()
    adapter.active = "active"
    with service._execution_lease("probe"), pytest.raises(nh.HelperError, match="sender"):
        service.handle(req("resolve-running"), peer_uid=record["uid"])


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_stopped_recovery_missing_sender_lock_refused(helper):
    service, _adapter, record = stopped_unknown(helper)
    (service.registry / "execution-probe.lock").unlink()
    with pytest.raises(FileNotFoundError):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()


@pytest.mark.parametrize("action", ["resolve-running", "resolve-stopped"])
@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_guard_missing_receipt_blocks_recovery(helper, action):
    service, adapter, record = stopped_unknown(helper)
    if action == "resolve-running":
        adapter.override.clear()
        adapter.active = "active"
    (service.registry / "sender-probe-one.json").unlink()
    with pytest.raises(nh.HelperError, match="receipt missing"):
        service.handle(req(action), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()
    with pytest.raises(nh.HelperError, match="unconfirmed"):
        service.handle(req("start", "bypass"), peer_uid=record["uid"])


@pytest.mark.parametrize("action", ["resolve-running", "resolve-stopped"])
@pytest.mark.parametrize("bad_code", ["missing", None, True, "0", 1.5, [], {}])
@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_guard_malformed_returncode_blocks_recovery(helper, action, bad_code):
    service, adapter, record = stopped_unknown(helper)
    if action == "resolve-running":
        adapter.override.clear()
        adapter.active = "active"
    path = service.registry / "sender-probe-one.json"
    receipt = json.loads(path.read_text())
    if bad_code == "missing":
        receipt.pop("returncode")
    else:
        receipt["returncode"] = bad_code
    path.write_text(json.dumps(receipt))
    intent = (service.registry / "op-probe-one.json").read_bytes()
    with pytest.raises(nh.HelperError, match="receipt mismatch"):
        service.handle(req(action), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()
    assert (service.registry / "op-probe-one.json").read_bytes() == intent
    with pytest.raises(nh.HelperError, match="unconfirmed"):
        service.handle(req("start", "bypass"), peer_uid=record["uid"])


@pytest.mark.parametrize("value", [None, [], "sender_reaped", 1])
@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_guard_non_object_receipt_blocks_recovery(helper, value):
    service, _adapter, record = stopped_unknown(helper)
    (service.registry / "sender-probe-one.json").write_text(json.dumps(value))
    with pytest.raises(nh.HelperError, match="receipt mismatch"):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()
    with pytest.raises(nh.HelperError, match="unconfirmed"):
        service.handle(req("start", "bypass"), peer_uid=record["uid"])


@pytest.mark.parametrize("returncode", [-9, 1, 124])
@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_guard_nonzero_integer_receipt_allows_only_observed_recovery(helper, returncode):
    service, _adapter, record = stopped_unknown(helper)
    path = service.registry / "sender-probe-one.json"
    receipt = json.loads(path.read_text())
    receipt["returncode"] = returncode
    path.write_text(json.dumps(receipt))
    result = service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    assert result["recovery"]["state"] == "confirmed_stopped"
    assert result["recovery"]["start_replayed"] is False


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_guard_receipt_mismatch_refused(helper):
    service, _adapter, record = stopped_unknown(helper)
    path = service.registry / "sender-probe-one.json"
    value = json.loads(path.read_text())
    value["intent"]["operation_id"] = "different"
    path.write_text(json.dumps(value))
    with pytest.raises(nh.HelperError, match="receipt mismatch"):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])


@pytest.mark.parametrize("barrier", [None, 1])
def test_legacy_running_recovery_without_sender_completion_remains_unknown(helper, barrier):
    service, adapter, record = stopped_unknown(helper)
    path = service.registry / "op-probe-one.json"
    intent = json.loads(path.read_text())
    if barrier is None:
        intent.pop("execution_barrier")
    else:
        intent["execution_barrier"] = barrier
    path.write_text(json.dumps(intent))
    (service.registry / "sender-probe-one.json").unlink()
    adapter.override.clear()
    adapter.active = "active"
    with pytest.raises(nh.HelperError, match="legacy.*sender"):
        service.handle(req("resolve-running"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()
    with pytest.raises(nh.HelperError, match="unconfirmed"):
        service.handle(req("start", "next"), peer_uid=record["uid"])


@pytest.mark.parametrize("barrier", [None, 1])
def test_existing_legacy_recovery_cannot_authorize_new_start(helper, barrier):
    service, adapter, record = stopped_unknown(helper)
    path = service.registry / "op-probe-one.json"
    intent = json.loads(path.read_text())
    if barrier is None:
        intent.pop("execution_barrier")
    else:
        intent["execution_barrier"] = barrier
    path.write_text(json.dumps(intent))
    recovery = service.registry / "recovery-probe-one.json"
    recovery.write_text(json.dumps({"state": "confirmed_running", "intent": intent, "start_replayed": False}))
    before = recovery.read_bytes()
    adapter.override.clear()
    adapter.active = "active"
    observed = service.handle(req("query"), peer_uid=record["uid"])
    assert observed["recovery"]["state"] == "confirmed_running"
    with pytest.raises(nh.HelperError, match="legacy.*sender"):
        service.handle(req("start", "next"), peer_uid=record["uid"])
    assert recovery.read_bytes() == before
    assert not (service.registry / "op-probe-next.json").exists()


def test_started_result_without_proven_sender_tree_blocks_new_start(helper):
    service, adapter, record = helper
    service.handle(req("start"), peer_uid=record["uid"])
    assert json.loads((service.registry / "result-probe-one.json").read_text())["state"] == "started"
    starts = sum(call[0] == "start" for call in adapter.calls)
    with pytest.raises(nh.HelperError, match="sender process tree completion unproven"):
        service.handle(req("start", "next"), peer_uid=record["uid"])
    assert sum(call[0] == "start" for call in adapter.calls) == starts
    assert not (service.registry / "op-probe-next.json").exists()
    assert service.handle(req("query"), peer_uid=record["uid"])["result"]["state"] == "started"


@pytest.mark.parametrize("action", ["resolve-running", "resolve-stopped"])
def test_direct_sender_receipt_does_not_authorize_recovery(helper, action):
    service, adapter, record = stopped_unknown(helper)
    if action == "resolve-running":
        adapter.override.clear()
        adapter.active = "active"
    with pytest.raises(nh.HelperError, match="sender process tree completion unproven"):
        service.handle(req(action), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()


def test_existing_direct_sender_recovery_cannot_authorize_new_start(helper):
    service, adapter, record = stopped_unknown(helper)
    intent = json.loads((service.registry / "op-probe-one.json").read_text())
    recovery = service.registry / "recovery-probe-one.json"
    recovery.write_text(json.dumps({"state": "confirmed_stopped", "intent": intent, "start_replayed": False}))
    before = recovery.read_bytes()
    starts_before = sum(call[0] == "start" for call in adapter.calls)
    observed = service.handle(req("query"), peer_uid=record["uid"])
    assert observed["recovery"]["state"] == "confirmed_stopped"
    with pytest.raises(nh.HelperError, match="sender process tree completion unproven"):
        service.handle(req("start", "next"), peer_uid=record["uid"])
    assert recovery.read_bytes() == before
    assert not (service.registry / "op-probe-next.json").exists()
    assert sum(call[0] == "start" for call in adapter.calls) == starts_before


def test_old_barrier_without_receipt_not_safe_for_stopped(helper):
    service, _adapter, record = stopped_unknown(helper)
    path = service.registry / "op-probe-one.json"
    value = json.loads(path.read_text())
    value["execution_barrier"] = 1
    path.write_text(json.dumps(value))
    with pytest.raises(nh.HelperError, match="legacy"):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])


def test_native_management_marker_drift_blocks_helper(helper):
    service, adapter, record = helper
    (Path(record["root"]) / nh.MARKER).write_text("changed")
    with pytest.raises(nh.HelperError, match="management binding"):
        service.handle(req("start"), peer_uid=record["uid"])
    assert not adapter.calls


@pytest.mark.parametrize("suffix", [".apply-intent.json", ".apply-intent.json.symlink"])
def test_package_intent_blocks_new_start_but_not_query(helper, suffix):
    manager, adapter, record = helper
    directory = manager.registry / "updates"
    directory.mkdir()
    path = directory / "other--update.apply-intent.json"
    if suffix.endswith("symlink"):
        path.symlink_to(directory / "missing")
    else:
        path.write_text("corrupt still blocks")
    with pytest.raises(nh.HelperError, match="package transaction blocks start"):
        manager.handle(req("start"), peer_uid=record["uid"])
    assert manager.handle(req("query"), peer_uid=record["uid"])["observation"]
    assert not any(call[0] == "start" for call in adapter.calls)
    assert not (manager.registry / "op-probe-one.json").exists()
    # Re-creating the daemon object does not release the persistent fence.
    again = nh.Helper(manager.registry, adapter=adapter)
    with pytest.raises(nh.HelperError, match="package transaction"):
        again.handle(req("start", "two"), peer_uid=record["uid"])


def test_old_start_id_is_observation_only_during_update(helper):
    manager, adapter, record = helper
    manager.handle(req("start"), peer_uid=record["uid"])
    directory = manager.registry / "updates"
    directory.mkdir()
    (directory / "probe--update.apply-intent.json").write_text("{}")
    result = manager.handle(req("start"), peer_uid=record["uid"])
    assert result["replayed"] is False
    assert len([call for call in adapter.calls if call[0] == "start"]) == 1


def test_package_exclusive_lease_blocks_start(helper):
    manager, adapter, record = helper
    with (
        nh.package_lease(manager.registry, exclusive=True),
        pytest.raises(nh.HelperError, match="still executing"),
    ):
        manager.handle(req("start"), peer_uid=record["uid"])
    assert not any(call[0] == "start" for call in adapter.calls)


def test_package_lock_shared_start_and_cross_process_exclusion(helper):
    manager, _, _ = helper
    import sys

    # A separate process cannot acquire exclusive access while a start owns SH.
    code = """import fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDWR)
try:
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(7)
"""
    with nh.package_lease(manager.registry), nh.package_lease(manager.registry):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(manager.registry / "package-manager.execution.lock"),
            ],
            check=False,
        )
        assert result.returncode == 7
    with nh.package_lease(manager.registry, exclusive=True):
        pass


def test_systemd_guard_inherits_package_lease(tmp_path):

    registry = tmp_path / "registry"
    registry.mkdir()
    executable = tmp_path / "systemctl"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    # Use owned temporary storage while exercising the actual guard process.
    from unittest.mock import patch

    with (
        patch.object(nh, "_trusted", lambda *a, **k: None),
        nh.package_lease(registry) as package_fd,
        (tmp_path / "execution.lock").open("a+b") as lease,
    ):
        real_run = subprocess.run

        def checked_run(*args, **kwargs):
            assert package_fd in kwargs["pass_fds"]
            return real_run(*args, **kwargs)

        with patch.object(nh.subprocess, "run", checked_run):
            nh.Systemd(str(executable)).start(
                "fixture",
                lease_fd=lease.fileno(),
                package_fd=package_fd,
                receipt_path=tmp_path / "receipt.json",
                intent={"fixture": True},
            )
    assert json.loads((tmp_path / "receipt.json").read_text())["state"] == "sender_reaped"


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux procfs")
def test_systemd_guard_and_sender_do_not_inherit_helper_sockets(tmp_path):
    import socket
    from unittest.mock import patch

    registry = tmp_path / "registry"
    registry.mkdir()
    endpoint = tmp_path / "helper.sock"
    report = tmp_path / "descriptor-report.json"
    executable = tmp_path / "inspect-sender"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(endpoint))
        listener.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(endpoint))
            connection, _ = listener.accept()
            with connection:
                # Exercise close_fds even if a future caller mistakenly marks sockets inheritable.
                os.set_inheritable(listener.fileno(), True)
                os.set_inheritable(connection.fileno(), True)
                identities = [(os.fstat(s.fileno()).st_dev, os.fstat(s.fileno()).st_ino)
                              for s in (listener, connection)]
                executable.write_text(
                    f"#!{sys.executable}\n"
                    "import json, os\n"
                    f"wanted = {identities!r}\n"
                    "def present(pid):\n"
                    "    found = []\n"
                    "    for name in os.listdir(f'/proc/{pid}/fd'):\n"
                    "        try:\n"
                    "            s = os.stat(f'/proc/{pid}/fd/{name}')\n"
                    "        except FileNotFoundError:\n"
                    "            continue\n"
                    "        if (s.st_dev, s.st_ino) in wanted:\n"
                    "            found.append(name)\n"
                    "    return found\n"
                    f"with open({str(report)!r}, 'w') as out:\n"
                    "    json.dump({'guard': present(os.getppid()), 'sender': present(os.getpid())}, out)\n"
                )
                executable.chmod(0o700)
                with (
                    patch.object(nh, "_trusted", lambda *args, **kwargs: None),
                    nh.package_lease(registry) as package_fd,
                    (tmp_path / "execution.lock").open("a+b") as lease,
                ):
                    nh.Systemd(str(executable)).start(
                        "fixture",
                        lease_fd=lease.fileno(),
                        package_fd=package_fd,
                        receipt_path=tmp_path / "receipt.json",
                        intent={"fixture": True},
                    )
    assert json.loads(report.read_text()) == {"guard": [], "sender": []}
    assert json.loads((tmp_path / "receipt.json").read_text())["state"] == "sender_reaped"


def test_start_rejected_while_job_publishes_under_exclusive_package_lock(helper):
    service, adapter, record = helper
    with (
        nh.package_lease(service.registry, exclusive=True),
        pytest.raises(nh.HelperError, match="still executing"),
    ):
        service.handle(req("start"), peer_uid=record["uid"])
    assert not (service.registry / "op-probe-one.json").exists()
    assert not any(action == "start" for action, _ in adapter.calls)


def test_published_job_intent_fences_start_after_package_lock_release(helper):
    service, adapter, record = helper
    updates = service.registry / "updates"
    updates.mkdir()
    with nh.package_lease(service.registry, exclusive=True):
        nh._save_new(updates / "fixture.job-intent.json", {"state": "job_dispatch_intent"})
    with pytest.raises(nh.HelperError, match="package transaction blocks start"):
        service.handle(req("start"), peer_uid=record["uid"])
    assert not (service.registry / "op-probe-one.json").exists()
    assert not any(action == "start" for action, _ in adapter.calls)


def test_inherited_package_lock_survives_callers_fd_close(helper):
    import sys

    manager, _, _ = helper
    with nh.package_lease(manager.registry) as fd:
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; print('ready',flush=True); sys.stdin.read()"],
            pass_fds=(fd,),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert child.stdout.readline().strip() == "ready"
    try:
        with (
            pytest.raises(nh.HelperError, match="still executing"),
            nh.package_lease(manager.registry, exclusive=True),
        ):
            pytest.fail("inherited shared lock lost")
    finally:
        child.communicate("", timeout=10)
    assert child.returncode == 0
    with nh.package_lease(manager.registry, exclusive=True):
        pass


@pytest.mark.parametrize(
    "state",
    ["unknown", "package_applied_pending_activation", "source_package_observed_recovery_required"],
)
def test_recovered_package_state_never_clears_start_fence(helper, state):
    manager, adapter, record = helper
    directory = manager.registry / "updates"
    directory.mkdir()
    (directory / "probe--update.apply-intent.json").write_text("{}")
    (directory / "probe--update.recovery.json").write_text(json.dumps({"state": state}))
    with pytest.raises(nh.HelperError, match="package transaction"):
        manager.handle(req("start"), peer_uid=record["uid"])
    assert not any(call[0] == "start" for call in adapter.calls)


@pytest.mark.parametrize("held_lock", [False, True])
def test_registration_blocked_by_package_execution_or_persistent_intent(
    helper, monkeypatch, held_lock
):
    from contextlib import nullcontext

    manager, _, record = helper
    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)
    called = []
    monkeypatch.setattr(nh, "_register", lambda *a, **k: called.append(True))
    if not held_lock:
        directory = manager.registry / "updates"
        directory.mkdir()
        (directory / "probe--update.apply-intent.json").write_text("{}")
    with (
        nh.package_lease(manager.registry, exclusive=True) if held_lock else nullcontext(),
        pytest.raises(nh.HelperError, match="still executing|package transaction"),
    ):
        nh.register(
            manager.registry,
            manager.registry,
            instance_id="second",
            root=record["root"],
            username="fixture",
            environment_file=record["environment_file"],
            recover=True,
        )
    assert called == []


def test_marker_readable_and_operation_receipt_private(tmp_path):
    marker = tmp_path / "marker.json"
    receipt = tmp_path / "operation.json"
    nh._save_new(marker, {"binding": "non-secret"}, mode=0o644)
    nh._save_new(receipt, {"operation": "private"})
    assert marker.stat().st_mode & 0o777 == 0o644
    assert receipt.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("barrier", [True, False, 1.0, 2.0, 0, 3, "2", None])
def test_invalid_start_barrier_blocks_replay_and_running_recovery(helper, barrier):
    service, adapter, record = helper
    adapter.failure = True
    service.handle(req("start"), peer_uid=record["uid"])
    intent_path = service.registry / "op-probe-one.json"
    intent = json.loads(intent_path.read_text())
    intent["execution_barrier"] = barrier
    intent_path.write_text(json.dumps(intent))
    receipt_path = service.registry / "sender-probe-one.json"
    if receipt_path.exists():
        receipt_path.unlink()
    adapter.failure = False
    adapter.active = "active"
    before = intent_path.read_bytes()
    calls_before = list(adapter.calls)
    with pytest.raises(nh.HelperError, match="barrier|identity|matching"):
        service.handle(req("resolve-running"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()
    with pytest.raises(nh.HelperError, match="barrier|identity|matching"):
        service.handle(req("start"), peer_uid=record["uid"])
    assert intent_path.read_bytes() == before
    assert sum(name == "start" for name, _ in adapter.calls) == sum(name == "start" for name, _ in calls_before)


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_sender_receipt_intent_type_drift_cannot_authorize_recovery(helper):
    service, _adapter, record = stopped_unknown(helper)
    receipt_path = service.registry / "sender-probe-one.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["intent"]["execution_barrier"] = 2.0
    receipt_path.write_text(json.dumps(receipt))
    before = receipt_path.read_bytes()
    with pytest.raises(nh.HelperError, match="receipt mismatch"):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    assert receipt_path.read_bytes() == before
    assert not (service.registry / "recovery-probe-one.json").exists()


@pytest.mark.usefixtures("legacy_start_recovery_mechanics")
def test_recovery_intent_type_drift_blocks_new_start(helper):
    service, adapter, record = stopped_unknown(helper)
    service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    path = service.registry / "recovery-probe-one.json"
    recovery = json.loads(path.read_text())
    recovery["intent"]["execution_barrier"] = 2.0
    path.write_text(json.dumps(recovery))
    before = path.read_bytes()
    old_calls = sum(action == "start" for action, _ in adapter.calls)
    with pytest.raises(nh.HelperError, match="invalid recovery evidence"):
        service.handle(req("start", "new-id"), peer_uid=record["uid"])
    assert path.read_bytes() == before
    assert not (service.registry / "op-probe-new-id.json").exists()
    assert sum(action == "start" for action, _ in adapter.calls) == old_calls


def test_invalid_start_barrier_blocks_new_operation(helper):
    service, adapter, record = stopped_unknown(helper)
    path = service.registry / "op-probe-one.json"
    intent = json.loads(path.read_text())
    intent["execution_barrier"] = True
    path.write_text(json.dumps(intent))
    before = path.read_bytes()
    starts = sum(action == "start" for action, _ in adapter.calls)
    with pytest.raises(nh.HelperError, match="invalid start intent barrier"):
        service.handle(req("start", "new-id"), peer_uid=record["uid"])
    assert path.read_bytes() == before
    assert not (service.registry / "op-probe-new-id.json").exists()
    assert sum(action == "start" for action, _ in adapter.calls) == starts


@pytest.mark.parametrize("drift", ["duplicate", "missing", "extra", "malformed", "large", "nonstring"])
def test_systemd_query_rejects_ambiguous_output(monkeypatch, drift):
    from types import SimpleNamespace

    lines = [
        "ActiveState=active", "SubState=running", "MainPID=123", "Job=",
        "User=12345", "Group=12345", "FragmentPath=/etc/systemd/system/probe.service",
        "ExecStart={ path=/fixture/run ; argv[]=/fixture/run client ; ignore_errors=no ; }",
    ]
    if drift == "duplicate":
        lines.insert(0, "ActiveState=failed")
    elif drift == "missing":
        lines = [line for line in lines if not line.startswith("Job=")]
    elif drift == "extra":
        lines.append("Unknown=value")
    elif drift == "malformed":
        lines.append("unparseable")
    elif drift == "large":
        lines.append("X=" + "x" * 17000)
    output = b"not text" if drift == "nonstring" else "\n".join(lines) + "\n"
    monkeypatch.setattr(nh.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=output))
    with pytest.raises(nh.HelperError, match="systemd.*observation"):
        nh.Systemd().query("probe.service")


def test_systemd_query_accepts_exact_fields_and_empty_job(monkeypatch):
    from types import SimpleNamespace

    output = (
        "ActiveState=active\nSubState=running\nMainPID=123\nJob=\n"
        "User=12345\nGroup=12345\nFragmentPath=/etc/systemd/system/probe.service\n"
        "ExecStart={ path=/fixture/run ; argv[]=/fixture/run client ; ignore_errors=no ; }\n"
    )
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(nh.subprocess, "run", run)
    result = nh.Systemd().query("probe.service")
    assert result["Job"] == "" and result["MainPID"] == "123"
    assert calls[0][0][:3] == ["/usr/bin/systemctl", "show", "probe.service"]
