import fcntl
import json
import os
import subprocess
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
    assert "native_helper serve" in result


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
    assert "ExecStartPre" in content and "ExecStartPre=" not in content
    assert "--group 12345" in content
    with pytest.raises(nh.HelperError, match="already exists"):
        nh.deploy_helper(**deployment)
    assert unit.read_text() == content


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


def test_recover_socket_requires_administrator(recovery, monkeypatch):
    monkeypatch.setattr(nh.os, "geteuid", lambda: 12345)
    with pytest.raises(nh.HelperError, match="administrator"):
        nh.recover_socket(*recovery)


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
def test_unknown_recovery_requires_running_evidence(helper, field, value):
    service, adapter, record = helper
    adapter.failure = True
    service.handle(req("start"), peer_uid=record["uid"])
    adapter.active = "active"
    adapter.override[field] = value
    with pytest.raises(nh.HelperError, match="remains unknown"):
        service.handle(req("resolve-running"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()


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


def test_stopped_recovery_missing_sender_lock_refused(helper):
    service, _adapter, record = stopped_unknown(helper)
    (service.registry / "execution-probe.lock").unlink()
    with pytest.raises(FileNotFoundError):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])
    assert not (service.registry / "recovery-probe-one.json").exists()


@pytest.mark.parametrize("action", ["resolve-running", "resolve-stopped"])
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


def test_guard_receipt_mismatch_refused(helper):
    service, _adapter, record = stopped_unknown(helper)
    path = service.registry / "sender-probe-one.json"
    value = json.loads(path.read_text())
    value["intent"]["operation_id"] = "different"
    path.write_text(json.dumps(value))
    with pytest.raises(nh.HelperError, match="receipt mismatch"):
        service.handle(req("resolve-stopped"), peer_uid=record["uid"])


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
