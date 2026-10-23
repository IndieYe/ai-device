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
