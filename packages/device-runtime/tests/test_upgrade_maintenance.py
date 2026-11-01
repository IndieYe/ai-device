"""No real systemd or process stop/start: isolated adapter and actual local sockets."""

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_installation import create
from test_instance_lifecycle import delivery as delivery  # noqa: PLC0414
from test_instance_lifecycle import simulated_installer as simulated_installer  # noqa: PLC0414
from test_instance_lifecycle import upgraded as upgraded  # noqa: PLC0414

from device_runtime import upgrade_supervisor as supervisor
from device_runtime import upgrader as up
from device_runtime.client import DeviceClient
from device_runtime.maintenance import MaintenanceServer, request


@pytest.fixture
def single(tmp_path, delivery, simulated_installer):
    root = tmp_path / "single"
    create(root, delivery, roles=("client",))
    return root


@pytest.mark.asyncio
async def test_real_socket_drain_resume_status():
    class Client:
        async def maintenance_drain(self, upgrade_id):
            return {"upgrade_id": upgrade_id, "drained": True}

        async def maintenance_resume(self, upgrade_id):
            return {"upgrade_id": upgrade_id, "resumed": True}

        async def maintenance_status(self, upgrade_id):
            return {"upgrade_id": upgrade_id, "pid": 42}

    # Short Unix paths exercise macOS's sockaddr length constraint too.
    with tempfile.TemporaryDirectory(prefix="du-") as directory:
        path = Path(directory) / "m.sock"
        server = MaintenanceServer(Client(), path)
        await server.start()
        try:
            assert path.stat().st_mode & 0o777 == 0o600
            assert (await asyncio.to_thread(request, path, "drain", "one"))["drained"]
            assert (await asyncio.to_thread(request, path, "status", "one"))["pid"] == 42
            assert (await asyncio.to_thread(request, path, "resume", "one"))["resumed"]
            with pytest.raises(ValueError, match="already exists"):
                await MaintenanceServer(Client(), path).start()
        finally:
            await server.close()
        assert not path.exists()


@pytest.mark.asyncio
async def test_drain_waits_both_requests_and_backgrounds(tmp_path):
    tasks = SimpleNamespace(count=1)

    async def active_count():
        return tasks.count

    tasks.active_count = active_count
    client = DeviceClient(
        server_url="ws://127.0.0.1:1",
        device_id="fixture",
        token="fixture-token-long-enough",
        state_dir=tmp_path / "state",
        executor=SimpleNamespace(tasks=tasks),
    )
    client._tasks.add(object())
    result = await client.maintenance_drain("one")
    assert not result["drained"] and result["active_tasks"] == 1
    client._tasks.clear()
    assert not (await client.maintenance_drain("one"))["drained"]
    tasks.count = 0
    assert (await client.maintenance_drain("one"))["drained"]
    with pytest.raises(ValueError, match="another"):
        await client.maintenance_drain("other")
    await client.maintenance_resume("one")
    assert client._maintenance_id is None
    await client.journal.close()


class Service:
    def __init__(self, root, fail=None):
        self.root = root
        self.fail = fail
        self.actions = []
        self.running = True

    def show(self):
        return {
            "ActiveState": "active" if self.running else "inactive",
            "MainPID": "7" if self.running else "0",
            "ExecStart": "{ path="
            + str(self.root / "run")
            + " ; argv[]="
            + str(self.root / "run")
            + " client ; ignore_errors=no ; }",
            "ControlGroup": "/test",
        }

    def action(self, action):
        self.actions.append(action)
        if self.fail == action:
            raise OSError("sensitive failure")
        self.running = action == "start"

    def owns_pid(self, pid, snapshot):
        return pid == 8


def setup_supervision(single, upgraded, monkeypatch):
    up.prepare(single, upgraded, "one")
    monkeypatch.setattr(supervisor, "profile", lambda root: {"unit": "fixture.service"})

    def local_request(sock, action, upgrade_id):
        return {
            "upgrade_id": upgrade_id,
            "drained": True,
            "pid": 8,
            "ready": True,
            "maintenance_owner": None,
            "python": str(
                single
                / "releases"
                / up.life._read(single / "active.json")["release"]
                / "client/venv/bin/python"
            ),
            "release": up.life._read(single / "active.json")["release"],
            "device_id": up.life._read(single / "instance.json")["instance_id"],
        }

    monkeypatch.setattr(supervisor, "request", local_request)


def test_supervisor_local_verification_not_central_success(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single)
    result = supervisor.supervise(single, "one", confirmed=True, adapter=service)
    assert result["state"] == "verified_local"
    assert service.actions == ["stop", "start"]
    assert supervisor.supervise(single, "one", confirmed=True, adapter=service) == result
    assert service.actions == ["stop", "start"]


@pytest.mark.parametrize("failure", ["stop", "start"])
def test_service_failure_is_unknown_never_replayed(single, upgraded, monkeypatch, failure):
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single, failure)
    with pytest.raises(up.UpgradeError, match="reconcile"):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    assert up.status(single, "one")["state"] == "unknown"
    count = len(service.actions)
    supervisor.supervise(single, "one", confirmed=True, adapter=service)
    assert len(service.actions) == count


def test_drain_timeout_does_not_stop(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    requests = []
    original = supervisor.request

    def busy(sock, action, upgrade_id):
        requests.append(action)
        if action == "status":
            return original(sock, action, upgrade_id)
        return {"drained": False}

    monkeypatch.setattr(supervisor, "request", busy)
    service = Service(single)
    with pytest.raises(up.UpgradeError):
        supervisor.supervise(single, "one", confirmed=True, adapter=service, timeout=0.01)
    assert not service.actions
    assert requests[-1] == "resume"
    assert up.status(single, "one")["state"] == "unknown"


@pytest.mark.asyncio
async def test_socket_close_preserves_replacement():
    with tempfile.TemporaryDirectory(prefix="du-") as directory:
        path = Path(directory) / "m.sock"
        server = MaintenanceServer(object(), path)
        await server.start()
        path.unlink()
        path.write_text("replacement")
        await server.close()
        assert path.read_text() == "replacement"


@pytest.mark.asyncio
async def test_socket_rejects_public_parent():
    with tempfile.TemporaryDirectory(prefix="du-") as directory:
        parent = Path(directory)
        parent.chmod(0o755)
        with pytest.raises(ValueError, match="private"):
            await MaintenanceServer(object(), parent / "m.sock").start()
        assert not (parent / "m.sock").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"[]\n", b"x" * 5000 + b"\n", b'{"action":"no"}\n'])
async def test_socket_malformed_request_is_bounded(payload):
    with tempfile.TemporaryDirectory(prefix="du-") as directory:
        path = Path(directory) / "m.sock"
        server = MaintenanceServer(object(), path)
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(path))
            writer.write(payload)
            await writer.drain()
            assert b"maintenance_unavailable" in await asyncio.wait_for(reader.readline(), 2)
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()


def test_health_pointer_alone_is_not_loaded_version(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    original = supervisor.request

    def wrong_python(*args):
        value = original(*args)
        value["python"] = "/unrelated/venv/bin/python"
        return value

    monkeypatch.setattr(supervisor, "request", wrong_python)
    service = Service(single)
    with pytest.raises(up.UpgradeError):
        supervisor.supervise(single, "one", confirmed=True, adapter=service, timeout=0.01)
    assert up.status(single, "one")["state"] == "prepared"
    assert service.actions == []


@pytest.mark.asyncio
async def test_maintenance_start_failure_closes_executor(tmp_path):
    closed = []

    async def close():
        closed.append(True)

    executor = SimpleNamespace(aclose=close)
    client = DeviceClient(
        server_url="ws://127.0.0.1:1",
        device_id="fixture",
        token="fixture-token-long-enough",
        state_dir=tmp_path / "state",
        executor=executor,
        maintenance=True,
    )
    # Journal construction may create its own directory; unknown path must survive.
    client.state_dir.mkdir(exist_ok=True)
    client.state_dir.chmod(0o700)
    marker = client.state_dir / "maintenance.sock"
    marker.write_text("existing")
    with pytest.raises(ValueError, match="already exists"):
        await client.run()
    assert closed == [True]
    assert marker.read_text() == "existing"


@pytest.mark.asyncio
async def test_live_frozen_admission_resume_and_duplicates(live_server, tmp_path):
    from conftest import FakeExecutor, running_client
    from test_websocket import payload, terminal

    _, _, http = live_server
    executor = FakeExecutor()

    async def no_background_tasks():
        return 0

    executor.tasks = SimpleNamespace(active_count=no_background_tasks)
    async with running_client(live_server, tmp_path / "client", executor) as runtime:
        assert (await runtime.maintenance_drain("one"))["drained"]
        await http.post("/v1/operations", json=payload(operation_id="op-frozen"))
        result = await terminal(http, "op-frozen")
        assert result["state"] == "failed"
        assert executor.calls == 0
        await runtime.maintenance_resume("one")
        await http.post("/v1/operations", json=payload(operation_id="op-open"))
        assert (await terminal(http, "op-open"))["state"] == "succeeded"
        assert executor.calls == 1
        await runtime.maintenance_drain("two")
        await http.post("/v1/operations", json=payload(operation_id="op-open"))
        assert (await terminal(http, "op-open"))["state"] == "succeeded"
        assert executor.calls == 1


@pytest.mark.asyncio
async def test_dead_socket_recovered_under_lifetime_lease():
    import socket

    with tempfile.TemporaryDirectory(prefix="du-") as directory:
        path = Path(directory) / "m.sock"
        with socket.socket(socket.AF_UNIX) as dead:
            dead.bind(str(path))
        server = MaintenanceServer(object(), path)
        await server.start()
        try:
            assert path.exists()
            with pytest.raises(ValueError, match="owner active"):
                await MaintenanceServer(object(), path).start()
        finally:
            await server.close()


@pytest.mark.asyncio
async def test_health_not_ready_before_initialize(tmp_path):
    client = DeviceClient(
        server_url="ws://127.0.0.1:1",
        device_id="fixture",
        token="fixture-token-long-enough",
        state_dir=tmp_path / "state",
        executor=SimpleNamespace(),
    )
    assert (await client.maintenance_status("one"))["ready"] is False
    await client.journal.close()


def test_policy_disabled_rejects_public_activate(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    (single / "upgrade-profile.json").write_text('{"allow_upgrade":false}')

    def disabled(root):
        raise up.UpgradeError("local policy disables upgrades")

    monkeypatch.setattr(supervisor, "profile", disabled)
    before = (single / "active.json").read_bytes()
    with pytest.raises(up.UpgradeError, match="disables"):
        up.activate(single, "one", stopped=True)
    assert (single / "active.json").read_bytes() == before


def test_public_activate_cannot_race_supervision(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    with (
        up._supervision_lease(single, exclusive=True),
        pytest.raises(up.UpgradeError, match="already running"),
    ):
        up.activate(single, "one", stopped=True)
    assert up.status(single, "one")["state"] == "prepared"


def test_wrong_unit_binding_rejected_before_drain(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single)
    original = service.show

    def wrong():
        snapshot = original()
        snapshot["ExecStart"] = snapshot["ExecStart"].replace("/run", "/runner-other")
        return snapshot

    monkeypatch.setattr(service, "show", wrong)
    with pytest.raises(up.UpgradeError, match="bound stable runner"):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    assert service.actions == []
    assert "supervisor" not in up.status(single, "one")


def test_lost_drain_response_durable_and_explicit_resume(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    original = supervisor.request
    owner = None

    def lost(sock, action, upgrade_id):
        nonlocal owner
        result = original(sock, action, upgrade_id)
        if action == "drain":
            owner = upgrade_id
            raise TimeoutError("response lost")
        if action == "resume":
            owner = None
        result["maintenance_owner"] = owner
        return result

    monkeypatch.setattr(supervisor, "request", lost)
    service = Service(single)
    with pytest.raises(up.UpgradeError, match="reconcile"):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    record = up.status(single, "one")
    assert record["state"] == "unknown"
    assert record["supervisor"]["phase"] == "draining"
    assert owner == "one" and not service.actions
    result = supervisor.resume_admission(single, "one", confirmed=True, adapter=service)
    assert owner is None and not service.actions
    assert result["state"] == "cancelled"


def test_real_cli_sigterm_cleans_socket_and_restarts():
    import os
    import subprocess
    import sys
    import time

    with tempfile.TemporaryDirectory(prefix="du-") as directory:
        state = Path(directory) / "client"
        state.mkdir(mode=0o700)
        env = {**os.environ, "DEVICE_RUNTIME_TOKEN": "test-fixture-not-a-real-token"}
        command = [
            sys.executable,
            "-m",
            "device_runtime.cli",
            "client",
            "--server-url",
            "ws://127.0.0.1:1",
            "--device-id",
            "fixture",
            "--state-dir",
            str(state),
            "--maintenance",
        ]
        for _ in range(2):
            process = subprocess.Popen(
                command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            try:
                deadline = time.monotonic() + 5
                while not (state / "maintenance.sock").exists():
                    assert process.poll() is None, process.communicate()
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
                process.terminate()  # Only this explicitly isolated test child.
                stdout, stderr = process.communicate(timeout=5)
                assert process.returncode == 0, (stdout, stderr)
                assert not (state / "maintenance.sock").exists()
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=5)


def test_reconcile_started_candidate_without_replay(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single, "start")
    with pytest.raises(up.UpgradeError):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    # The start may have completed before its caller lost the receipt.
    service.running = True
    actions = list(service.actions)
    record = supervisor.reconcile_supervision(single, "one", adapter=service)
    assert record["state"] == "verified_local"
    assert service.actions == actions
    assert supervisor.reconcile_supervision(single, "one", adapter=service) == record


def test_reconcile_stopped_candidate_remains_unknown(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single, "start")
    with pytest.raises(up.UpgradeError):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    record = supervisor.reconcile_supervision(single, "one", adapter=service)
    assert record["state"] == "unknown"
    assert record["code"] == "published_release_health_unconfirmed"
    assert service.actions == ["stop", "start"]


def test_reconcile_rejects_pointer_and_config_drift(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single, "start")
    with pytest.raises(up.UpgradeError):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    pointer = up.life._read(single / "active.json")
    pointer["activation_id"] = "f" * 32
    up._save(single / "active.json", pointer)
    service.running = True
    record = supervisor.reconcile_supervision(single, "one", adapter=service)
    assert record["state"] == "unknown"
    assert record["code"] == "active_pointer_conflict"
    config = up.life._read(single / "config/roles.json")
    config["client"]["server_url"] = "wss://other.invalid"
    up._save(single / "config/roles.json", config)
    with pytest.raises(up.UpgradeError, match="binding changed"):
        supervisor.reconcile_supervision(single, "one", adapter=service)
    assert service.actions == ["stop", "start"]


def test_local_release_policy_rechecked_before_stop(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    record = up.status(single, "one")
    policy = {
        "channel": "stable",
        "allowed_versions": [record["candidate"]["release_version"]],
        "bundle_channels": {record["request"]["bundle_sha256"]: "stable"},
        "maintenance_windows_utc": [
            {"start": "2000-01-01T00:00:00Z", "end": "2001-01-01T00:00:00Z"}
        ],
    }
    (single / "upgrade-profile.json").write_text("{}")
    monkeypatch.setattr(supervisor, "profile", lambda root: {"unit": "fixture.service", **policy})
    service = Service(single)
    before = (single / "active.json").read_bytes()
    with pytest.raises(up.UpgradeError, match="outside"):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    assert not service.actions
    assert (single / "active.json").read_bytes() == before
    with pytest.raises(up.UpgradeError, match="outside"):
        up.activate(single, "one", stopped=True)
    assert (single / "active.json").read_bytes() == before


@pytest.mark.parametrize("owner", ["one", "another"])
def test_reconcile_frozen_candidate_not_healthy(single, upgraded, monkeypatch, owner):
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single, "start")
    with pytest.raises(up.UpgradeError):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    service.running = True
    original = supervisor.request

    def frozen(*args):
        health = original(*args)
        health["maintenance_owner"] = owner
        return health

    monkeypatch.setattr(supervisor, "request", frozen)
    assert supervisor.reconcile_supervision(single, "one", adapter=service)["state"] == "unknown"
    assert service.actions == ["stop", "start"]


def record_hash(root):
    import hashlib

    return hashlib.sha256((root / "upgrades/one.json").read_bytes()).hexdigest()


def test_explicit_recovery_publish_then_start(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single, "stop")
    with pytest.raises(up.UpgradeError):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    # Operator establishes actual stop independently; adapter models that observation.
    service.running = False
    service.fail = None
    digest = record_hash(single)
    result = supervisor.recover(
        single,
        "one",
        action="publish",
        expected_record_sha256=digest,
        confirmed=True,
        adapter=service,
    )
    assert result["state"] == "published"
    assert service.actions == ["stop"]
    with pytest.raises(up.UpgradeError, match="record changed"):
        supervisor.recover(
            single,
            "one",
            action="publish",
            expected_record_sha256=digest,
            confirmed=True,
            adapter=service,
        )
    result = supervisor.recover(
        single,
        "one",
        action="start-candidate",
        expected_record_sha256=record_hash(single),
        confirmed=True,
        adapter=service,
    )
    assert result["state"] == "unknown"
    assert service.actions == ["stop", "start"]
    assert (
        supervisor.reconcile_supervision(single, "one", adapter=service)["state"]
        == "verified_local"
    )


def test_explicit_recovery_original_not_rollback(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single, "stop")
    with pytest.raises(up.UpgradeError):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    service.running = False
    service.fail = None
    before = (single / "active.json").read_bytes()
    supervisor.recover(
        single,
        "one",
        action="start-original",
        expected_record_sha256=record_hash(single),
        confirmed=True,
        adapter=service,
    )
    assert (single / "active.json").read_bytes() == before
    assert (
        supervisor.resume_admission(single, "one", confirmed=True, adapter=service)["state"]
        == "cancelled"
    )


def test_explicit_recovery_rejects_running_and_wrong_pointer(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single, "start")
    with pytest.raises(up.UpgradeError):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    count = len(service.actions)
    with pytest.raises(up.UpgradeError, match="no rollback"):
        supervisor.recover(
            single,
            "one",
            action="start-original",
            expected_record_sha256=record_hash(single),
            confirmed=True,
            adapter=service,
        )
    service.running = True
    with pytest.raises(up.UpgradeError, match="must be stopped"):
        supervisor.recover(
            single,
            "one",
            action="start-candidate",
            expected_record_sha256=record_hash(single),
            confirmed=True,
            adapter=service,
        )
    assert len(service.actions) == count


def test_original_start_lost_receipt_can_resume_without_replay(single, upgraded, monkeypatch):
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single, "stop")
    with pytest.raises(up.UpgradeError):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    service.running = False
    original_action = service.action

    def lost_start(action):
        service.actions.append(action)
        service.running = True
        raise TimeoutError("receipt lost")

    monkeypatch.setattr(service, "action", lost_start)
    with pytest.raises(up.UpgradeError, match="unconfirmed"):
        supervisor.recover(
            single,
            "one",
            action="start-original",
            expected_record_sha256=record_hash(single),
            confirmed=True,
            adapter=service,
        )
    actions = list(service.actions)
    monkeypatch.setattr(service, "action", original_action)
    assert (
        supervisor.resume_admission(single, "one", confirmed=True, adapter=service)["state"]
        == "cancelled"
    )
    assert service.actions == actions


def interrupt_publication(single, upgraded, monkeypatch):
    """Persist the actual publication intent, then fail before pointer replacement."""
    setup_supervision(single, upgraded, monkeypatch)
    service = Service(single)
    save = up._save

    def fail(path, value):
        if path == single / "active.json":
            raise OSError("pointer write interrupted")
        return save(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(up, "_save", fail)
        with pytest.raises(up.UpgradeError):
            supervisor.supervise(single, "one", confirmed=True, adapter=service)
    record = up.status(single, "one")
    assert record["state"] == "unknown"
    assert record["expected_active"] != up.life._read(single / "active.json")
    assert service.actions == ["stop"]
    return service, record["expected_active"]


def recover_publish(single, service):
    return supervisor.recover(
        single,
        "one",
        action="publish",
        expected_record_sha256=record_hash(single),
        confirmed=True,
        adapter=service,
    )


@pytest.mark.parametrize("second_fault", [None, "before", "after"])
def test_p1_reuses_intent_across_recovery_interruptions(
    single, upgraded, monkeypatch, second_fault
):
    service, pointer = interrupt_publication(single, upgraded, monkeypatch)
    with pytest.raises(up.UpgradeError, match="reconciliation"):
        up.activate(single, "one", stopped=True)
    with pytest.raises(up.UpgradeError, match="no rollback"):
        supervisor.recover(
            single,
            "one",
            action="start-original",
            expected_record_sha256=record_hash(single),
            confirmed=True,
            adapter=service,
        )
    supervisor.reconcile_supervision(single, "one", adapter=service)
    digest = record_hash(single)
    save = up._save
    writes = []

    def fault(path, value):
        if path == single / "active.json":
            if second_fault == "before":
                raise OSError("repeat pointer failure")
            writes.append(value)
        if second_fault == "after" and path.name == "one.json" and value["state"] == "published":
            raise OSError("published receipt lost")
        return save(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(up, "_save", fault)
        if second_fault:
            with pytest.raises(up.UpgradeError, match="unconfirmed"):
                recover_publish(single, service)
        else:
            assert recover_publish(single, service)["state"] == "published"
    assert up.status(single, "one")["expected_active"] == pointer
    assert service.actions == ["stop"]
    with pytest.raises(up.UpgradeError, match="record changed"):
        supervisor.recover(
            single,
            "one",
            action="publish",
            expected_record_sha256=digest,
            confirmed=True,
            adapter=service,
        )
    if second_fault == "before":
        supervisor.reconcile_supervision(single, "one", adapter=service)
        assert recover_publish(single, service)["state"] == "published"
    else:
        assert writes == [pointer]
    assert up.life._read(single / "active.json") == pointer
    with pytest.raises(up.UpgradeError, match="pointer changed"):
        recover_publish(single, service)
    supervisor.recover(
        single,
        "one",
        action="start-candidate",
        expected_record_sha256=record_hash(single),
        confirmed=True,
        adapter=service,
    )
    assert service.actions == ["stop", "start"]
    assert (
        supervisor.reconcile_supervision(single, "one", adapter=service)["state"]
        == "verified_local"
    )
    assert up.life._read(single / "active.json") == pointer


@pytest.mark.parametrize("change", ["activation", "candidate", "extra", "missing", "map", "null"])
def test_p1_invalid_pointer_rejected_before_recovery_intent(single, upgraded, monkeypatch, change):
    service, _ = interrupt_publication(single, upgraded, monkeypatch)
    path = single / "upgrades/one.json"
    record = up.status(single, "one")
    pointer = record["expected_active"]
    if change == "activation":
        pointer["activation_id"] = "invalid"
    elif change == "candidate":
        pointer["release"] = "different"
    elif change == "extra":
        pointer["extra"] = True
    elif change == "missing":
        pointer.pop("roles")
    elif change == "map":
        pointer["task_output_map_sha256"] = "0" * 64
    else:
        record["expected_active"] = None
    up._save(path, record)
    before = path.read_bytes()
    with pytest.raises(up.UpgradeError, match="invalid expected active"):
        recover_publish(single, service)
    assert path.read_bytes() == before
    assert service.actions == ["stop"]


@pytest.mark.parametrize("guard", ["running", "policy", "candidate", "pointer", "unit", "config"])
def test_p1_existing_recovery_guards_preserved(single, upgraded, monkeypatch, guard):
    service, pointer = interrupt_publication(single, upgraded, monkeypatch)
    path = single / "upgrades/one.json"
    if guard == "running":
        service.running = True
    elif guard == "policy":

        def reject(*args, **kwargs):
            raise up.UpgradeError("policy expired")

        monkeypatch.setattr(up, "_policy", reject)
    elif guard == "candidate":
        monkeypatch.setattr(up.life, "_check_release", lambda *args: {})
    elif guard == "pointer":
        up._save(single / "active.json", {**pointer, "activation_id": "f" * 32})
    elif guard == "unit":
        show = service.show
        monkeypatch.setattr(service, "show", lambda: {**show(), "ExecStart": "wrong"})
    else:
        baseline = up._baseline
        monkeypatch.setattr(
            up, "_baseline", lambda root: {**baseline(root), "config_sha256": "changed"}
        )
    before = path.read_bytes()
    with pytest.raises(up.UpgradeError):
        recover_publish(single, service)
    assert path.read_bytes() == before
    assert service.actions == ["stop"]


@pytest.mark.parametrize("drift", ["record", "pointer", "candidate"])
def test_p1_rechecks_under_install_lock(single, upgraded, monkeypatch, drift):
    from contextlib import contextmanager

    service, pointer = interrupt_publication(single, upgraded, monkeypatch)
    real = up.install._lock
    before = (single / "active.json").read_bytes()

    @contextmanager
    def lock(root):
        with real(root):
            if drift == "record":
                path = single / "upgrades/one.json"
                value = up.status(single, "one")
                value["changed"] = True
                up._save(path, value)
            elif drift == "pointer":
                up._save(single / "active.json", {**pointer, "activation_id": "f" * 32})
            else:
                monkeypatch.setattr(up.life, "_check_release", lambda *args: {})
            yield

    monkeypatch.setattr(up.install, "_lock", lock)
    with pytest.raises(up.UpgradeError):
        recover_publish(single, service)
    assert service.actions == ["stop"]
    assert up.status(single, "one")["expected_active"] == pointer
    if drift != "pointer":
        assert (single / "active.json").read_bytes() == before


@pytest.mark.parametrize("mapping", [None, "a" * 64])
def test_publication_pointer_preserves_exact_original_output_map(mapping):
    candidate = {"release": "candidate", "roles": ["client"], "bundle_sha256": "b" * 64}
    original = {"release": "original"}
    pointer = {**candidate, "activation_id": "1" * 32}
    if mapping is not None:
        original["task_output_map_sha256"] = mapping
        pointer["task_output_map_sha256"] = mapping
    record = {"candidate": candidate, "baseline": {"active": original}, "expected_active": pointer}
    assert up._publication_pointer(record) is pointer
    if mapping is not None:
        pointer.pop("task_output_map_sha256")
        with pytest.raises(up.UpgradeError, match="invalid expected active"):
            up._publication_pointer(record)


class LaunchdFixture:
    """Stands in for launchctl without touching a real launchd job."""

    forced_restart = True

    def __init__(self, root, macos_profile=False):
        self.root = root
        self.actions = []
        self.running = True
        self.macos_profile = macos_profile
        self._pid = 7

    def show(self):
        if not self.running:
            return {"ActiveState": "inactive", "SubState": "dead", "MainPID": "0"}
        return {
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": str(self._pid),
            "ExecStart": "",
            "ControlGroup": "",
        }

    def owns_pid(self, pid, snapshot):
        return snapshot.get("MainPID") == str(pid)

    def action(self, name):
        self.actions.append(name)
        if name == "stop":
            # A forced stop is a kill: the job is gone immediately, with no
            # grace period and no chance to finish in-flight work.
            self.running = False
            self._pid = 0
        else:
            self.running = True
            self._pid = 9


def _write_launchd_plist(root):
    import plistlib

    directory = root / "launchd"
    directory.mkdir(exist_ok=True)
    path = directory / f"com.device-runtime.{root.name}.plist"
    path.write_bytes(
        plistlib.dumps({"ProgramArguments": [str(root / "run"), "client"]})
    )
    return path


def _launchd_health(root, record, pid):
    return {
        "upgrade_id": "one",
        "pid": pid,
        "ready": True,
        "maintenance_owner": None,
        "python": str(
            root / "releases" / up.life._read(root / "active.json")["release"]
            / "client/venv/bin/python"
        ),
        "release": up.life._read(root / "active.json")["release"],
        "device_id": up.life._read(root / "instance.json")["instance_id"],
        "drained": True,
    }


def test_launchd_profile_binds_label_and_rejects_others(tmp_path, monkeypatch):
    import json

    root = tmp_path / "inst"
    root.mkdir()
    path = root / "upgrade-profile.json"
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "role": "client",
                "root": str(root),
                "label": "com.device-runtime.inst",
                "allow_upgrade": True,
            }
        )
    )
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    assert supervisor.profile(root)["label"] == "com.device-runtime.inst"
    # A systemd-style profile is not a launchd binding.
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "role": "client",
                "root": str(root),
                "unit": "device.service",
                "allow_upgrade": True,
            }
        )
    )
    with pytest.raises(up.UpgradeError, match="binding mismatch"):
        supervisor.profile(root)


def test_launchd_profile_rejects_world_writable(tmp_path, monkeypatch):
    import json

    root = tmp_path / "inst"
    root.mkdir()
    path = root / "upgrade-profile.json"
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "role": "client",
                "root": str(root),
                "label": "com.device-runtime.inst",
                "allow_upgrade": True,
            }
        )
    )
    path.chmod(0o666)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    with pytest.raises(up.UpgradeError, match="not writable by others"):
        supervisor.profile(root)


def test_launchd_service_key_is_platform_neutral():
    assert supervisor._service_key({"label": "com.x.y"}) == "com.x.y"
    assert supervisor._service_key({"unit": "x.service"}) == "x.service"


def test_runner_bound_reads_launchd_plist(tmp_path):
    root = tmp_path / "inst"
    root.mkdir()
    assert not supervisor._runner_bound(root, {"ExecStart": ""})
    _write_launchd_plist(root)
    assert supervisor._runner_bound(root, {"ExecStart": ""})


def test_runner_bound_rejects_plist_with_wrong_program(tmp_path):
    import plistlib

    root = tmp_path / "inst"
    (root / "launchd").mkdir(parents=True)
    (root / "launchd" / f"com.device-runtime.{root.name}.plist").write_bytes(
        plistlib.dumps({"ProgramArguments": ["/bin/sh", "-c", "something else"]})
    )
    assert not supervisor._runner_bound(root, {"ExecStart": ""})


def test_forced_restart_skips_drain_and_kills(single, upgraded, monkeypatch):
    """The launchd path must not wait for a drain that the kill makes moot."""
    up.prepare(single, upgraded, "one")
    monkeypatch.setattr(supervisor, "profile", lambda root: {"label": "com.device-runtime.x"})
    _write_launchd_plist(single)
    asked = []
    # The old client reports PID 7; the restarted candidate reports PID 9.
    # Health is asked more than once, so the answer has to depend on the phase
    # the supervisor is in rather than on a call counter.
    def health(sock, action, upgrade_id):
        asked.append(action)
        if action == "drain":
            raise AssertionError("forced restart must not drain")
        running = service.show()["MainPID"]
        return _launchd_health(single, None, int(running))

    monkeypatch.setattr(supervisor, "request", health)
    service = LaunchdFixture(single)
    result = supervisor.supervise(single, "one", confirmed=True, adapter=service)
    assert result["state"] == "verified_local"
    assert service.actions == ["stop", "start"]
    assert "drain" not in asked


def test_launchd_stop_failure_is_unknown_not_replayed(single, upgraded, monkeypatch):
    up.prepare(single, upgraded, "one")
    monkeypatch.setattr(supervisor, "profile", lambda root: {"label": "com.device-runtime.x"})
    _write_launchd_plist(single)
    monkeypatch.setattr(
        supervisor, "request", lambda sock, action, upgrade_id: _launchd_health(single, None, 7)
    )

    class Failing(LaunchdFixture):
        def action(self, name):
            self.actions.append(name)
            if name == "stop":
                raise OSError("launchctl unavailable")
            self.running = True

    service = Failing(single)
    with pytest.raises(up.UpgradeError, match="reconcile"):
        supervisor.supervise(single, "one", confirmed=True, adapter=service)
    record = up.status(single, "one")
    assert record["state"] == "unknown"
    assert record["code"] == "supervised_maintenance_unconfirmed"
