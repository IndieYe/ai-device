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
            "User": str(self.record["uid"]),
            "Group": str(self.record["gid"]),
            "FragmentPath": self.record["unit_path"],
            "ExecStart": f"{{ path={root}/run ; argv[]={root}/run client ; ignore_errors=no ; }}",
            **self.override,
        }

    def start(self, unit):
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
    record = {
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
