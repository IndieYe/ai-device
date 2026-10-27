import json
import subprocess
from contextlib import contextmanager

import pytest

from device_runtime import native_helper as nh
from device_runtime import native_update as nu


class Packages:
    def __init__(self):
        self.observation = {
            "status": "install ok installed",
            "version": "1",
            "architecture": "amd64",
        }
        self.calls = 0
        self.mode = "success"

    def metadata(self, path):
        return {"Package": "ai-device-native-client", "Version": "2", "Architecture": "amd64"}

    def installed(self, name):
        return self.observation.copy()

    def apply(self, path, *, lease_fd, receipt_path, intent):
        self.calls += 1
        if self.mode != "failure":
            self.observation["version"] = "2"
        if self.mode != "crash":
            nh._save_new(
                receipt_path,
                {
                    "state": "sender_reaped",
                    "intent": intent,
                    "returncode": 1 if self.mode == "failure" else 0,
                },
            )
        if self.mode != "success":
            raise subprocess.CalledProcessError(1, "fixture")


class Helper:
    def __init__(self, root):
        self.root = root
        self.state = {"ActiveState": "inactive", "SubState": "dead", "MainPID": "0", "Job": ""}

    @contextmanager
    def _lease(self, instance_id):
        yield

    def _binding(self, instance_id):
        return {"root": str(self.root), "instance_id": instance_id}

    def _snapshot(self, binding):
        return self.state.copy()


@pytest.fixture
def update(tmp_path, monkeypatch):
    monkeypatch.setattr(nu.sys, "platform", "linux")
    monkeypatch.setattr(nu.os, "geteuid", lambda: 0)
    monkeypatch.setattr(nh, "_trusted", lambda *a, **kw: None)
    registry = tmp_path / "registry"
    registry.mkdir()
    root = tmp_path / "instance"
    root.mkdir()
    (root / "active.json").write_text('{"release":"old"}')
    (root / ".installation.lock").touch()
    (registry / "probe.json").write_text('{"binding":"fixture"}')
    package = tmp_path / "candidate.deb"
    package.write_bytes(b"fixture package")
    packages = Packages()
    helper = Helper(root)
    manager = nu.Updates(registry, packages=packages, helper=helper)
    manager.prepare("probe", "one", package, sha256=nh._digest(package), version="2")
    prepared = registry / "updates/5-probe--one.prepared.json"
    manager.approve("probe", "one", prepared_sha256=nh._digest(prepared))
    return manager, packages, helper, package


def test_apply_requires_approval(update):
    manager, packages, _, _ = update
    (manager.registry / "updates/5-probe--one.approved.json").unlink()
    with pytest.raises(FileNotFoundError):
        manager.apply("probe", "one")
    assert packages.calls == 0


def test_apply_and_same_id_no_replay(update):
    manager, packages, helper, _ = update
    old = (helper.root / "active.json").read_bytes()
    result = manager.apply("probe", "one")
    assert result["outcome"]["state"] == "package_applied_pending_activation"
    assert result["activated"] is False
    manager.apply("probe", "one")
    assert packages.calls == 1 and (helper.root / "active.json").read_bytes() == old


@pytest.mark.parametrize(
    "mode,state",
    [
        ("lost", "package_applied_pending_activation"),
        ("failure", "source_package_observed_recovery_required"),
    ],
)
def test_failed_or_lost_completion_recover_without_reapply(update, mode, state):
    manager, packages, _, _ = update
    packages.mode = mode
    assert manager.apply("probe", "one")["outcome"]["state"] == "unknown"
    assert manager.reconcile("probe", "one")["recovery"] is None
    assert manager.recover("probe", "one")["recovery"]["state"] == state
    manager.recover("probe", "one")
    assert packages.calls == 1


def test_sender_crash_no_receipt_stays_blocked(update):
    manager, packages, _, _ = update
    packages.mode = "crash"
    manager.apply("probe", "one")
    with pytest.raises(FileNotFoundError):
        manager.recover("probe", "one")
    manager.apply("probe", "one")
    assert packages.calls == 1


@pytest.mark.parametrize("change", ["active", "package", "binding", "running", "job"])
def test_stale_inputs_or_live_service_refused(update, change):
    manager, packages, helper, package = update
    if change == "active":
        (helper.root / "active.json").write_text("changed")
    if change == "package":
        package.write_bytes(b"changed")
    if change == "binding":
        (manager.registry / "probe.json").write_text("changed")
    if change == "running":
        helper.state.update(ActiveState="active", MainPID="123")
    if change == "job":
        helper.state["Job"] = "42"
    with pytest.raises(nu.UpdateError):
        manager.apply("probe", "one")
    assert packages.calls == 0


def test_partial_database_recovery_refused(update):
    manager, packages, _, _ = update
    packages.mode = "failure"
    manager.apply("probe", "one")
    packages.observation["status"] = "install ok half-configured"
    with pytest.raises(nu.UpdateError, match="partial"):
        manager.recover("probe", "one")
    assert not (manager.registry / "updates/5-probe--one.recovery.json").exists()


def test_new_id_cannot_bypass_unknown(update):
    manager, packages, _, package = update
    packages.mode = "failure"
    manager.apply("probe", "one")
    manager.prepare("probe", "two", package, sha256=nh._digest(package), version="2")
    prepared = manager.registry / "updates/5-probe--two.prepared.json"
    manager.approve("probe", "two", prepared_sha256=nh._digest(prepared))
    with pytest.raises(nu.UpdateError, match="existing package"):
        manager.apply("probe", "two")
    assert packages.calls == 1


def test_multi_instance_shared_package_refused(update):
    manager, packages, _, _ = update
    (manager.registry / "second.json").write_text("{}")
    with pytest.raises(nu.UpdateError, match="exactly one"):
        manager.apply("probe", "one")
    assert packages.calls == 0
    assert not (manager.registry / "updates/5-probe--one.apply-intent.json").exists()


def test_unknown_start_blocks_update_even_when_service_is_inactive(update):
    manager, packages, _, _ = update
    (manager.registry / "op-probe-start.json").write_text('{"action":"start"}')
    with pytest.raises(nu.UpdateError, match="unconfirmed start"):
        manager.apply("probe", "one")
    assert packages.calls == 0


def test_shared_start_sender_lease_blocks_package_apply(update):
    manager, packages, _, _ = update
    with (
        nh.package_lease(manager.registry),
        pytest.raises(nu.UpdateError, match="still executing"),
    ):
        manager.apply("probe", "one")
    assert packages.calls == 0


def test_transaction_key_has_no_separator_alias(update):
    manager, _, _, _ = update
    # These pairs had the same raw concatenation: a--b--c.
    with manager._transaction("a--b", "c") as (_, first):
        pass
    with manager._transaction("a", "b--c") as (_, second):
        pass
    assert first != second


def test_recovery_rejects_intent_not_bound_to_preparation(update):
    manager, packages, _, _ = update
    packages.mode = "failure"
    manager.apply("probe", "one")
    intent = manager.registry / "updates/5-probe--one.apply-intent.json"
    intent.write_text('{"prepared_sha256":"changed"}')
    # Even a matching sender receipt cannot authorize a changed preparation.
    sender = manager.registry / "updates/5-probe--one.sender.json"
    sender.write_text('{"state":"sender_reaped","intent":{"prepared_sha256":"changed"}}')
    with pytest.raises(nu.UpdateError, match="approved preparation"):
        manager.recover("probe", "one")
    assert packages.calls == 1


@pytest.fixture
def signed_update(update, monkeypatch):
    import hashlib

    from device_runtime.protocol import canonical

    manager, _, helper, package = update
    root = helper.root
    (root / "active.json").write_text('{"release":"old","release_version":"1"}')
    (root / nh.MARKER).write_text("binding")
    envelope = manager.registry / "release.json"
    envelope.write_text("{}")
    home = root / "releases/new"
    (home / "bundle").mkdir(parents=True)
    manifest = {"components": "fixture"}
    (home / "bundle/manifest.json").write_text(canonical(manifest))
    python = home / "client/venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("fixture interpreter")
    python.chmod(0o700)
    prepared = {"release": "new", "bundle_sha256": nu.core._fingerprint(manifest)}
    (home / "installation.json").write_text(canonical(prepared))
    payload = {
        "package": {
            "sha256": nh._digest(package),
            "version": "2",
            "manifest_sha256": nh._digest(home / "bundle/manifest.json"),
        }
    }
    digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
    request = {
        "release_sha256": digest,
        "source_active_sha256": nh._digest(root / "active.json"),
        "binding_sha256": nh._digest(root / nh.MARKER),
    }
    receipt = root / "native-candidates/runtime.result.json"
    receipt.parent.mkdir()
    receipt.write_text(
        canonical(
            {
                "state": "runtime_prepared",
                "activated": False,
                "candidate": prepared,
                "request": request,
            }
        )
    )
    real_binding = helper._binding
    monkeypatch.setattr(
        helper, "_binding", lambda name: {**real_binding(name), "uid": receipt.stat().st_uid}
    )
    monkeypatch.setattr(nu.nc, "verify_release", lambda *a, **kw: payload)
    monkeypatch.setattr(nu.nc, "prepare_candidate", lambda *a, **kw: {"release_sha256": digest})
    monkeypatch.setattr(nu.core, "inspect_bundle", lambda _: manifest)
    return manager, package, envelope, receipt, home


def signed_prepare(signed_update):
    manager, package, envelope, receipt, _ = signed_update
    return manager.prepare_signed(
        "probe",
        "signed",
        package,
        envelope_path=envelope,
        trusted_keys={},
        release_id="new",
        runtime_receipt=receipt,
        bootstrap_version="1",
    )


def test_signed_preparation_binds_runtime_and_approval(signed_update):
    manager, _, _, receipt, _ = signed_update
    result = signed_prepare(signed_update)
    evidence = result["authenticated_candidate"]
    assert evidence["receipt_sha256"] == nh._digest(receipt)
    path = manager.registry / "updates/5-probe--signed.prepared.json"
    manager.approve("probe", "signed", prepared_sha256=nh._digest(path))
    assert signed_prepare(signed_update) == result


@pytest.mark.parametrize(
    "file", ["receipt", "installation", "manifest", "python", "active", "marker"]
)
def test_signed_evidence_drift_blocks_approval(signed_update, file):
    manager, _, _, receipt, home = signed_update
    signed_prepare(signed_update)
    path = manager.registry / "updates/5-probe--signed.prepared.json"
    locations = {
        "receipt": receipt,
        "installation": home / "installation.json",
        "manifest": home / "bundle/manifest.json",
        "python": home / "client/venv/bin/python",
        "active": home.parent.parent / "active.json",
        "marker": home.parent.parent / nh.MARKER,
    }
    locations[file].write_text("drift")
    with pytest.raises(nu.UpdateError, match="changed"):
        manager.approve("probe", "signed", prepared_sha256=nh._digest(path))
    assert not path.with_name("5-probe--signed.approved.json").exists()


def test_signed_approval_rechecks_actual_bundle(signed_update, monkeypatch):
    manager, _, _, _, _ = signed_update
    signed_prepare(signed_update)
    path = manager.registry / "updates/5-probe--signed.prepared.json"

    def changed(_):
        raise ValueError("wheel digest differs")

    monkeypatch.setattr(nu.core, "inspect_bundle", changed)
    with pytest.raises(ValueError, match="wheel digest"):
        manager.approve("probe", "signed", prepared_sha256=nh._digest(path))


def test_signature_failure_creates_no_prepared_record(signed_update, monkeypatch):
    def invalid(*a, **kw):
        raise nu.nc.CandidateError("invalid signature")

    monkeypatch.setattr(nu.nc, "verify_release", invalid)
    with pytest.raises(nu.nc.CandidateError, match="signature"):
        signed_prepare(signed_update)
    assert not (signed_update[0].registry / "updates/5-probe--signed.prepared.json").exists()


def test_runtime_receipt_wrong_source_refused(signed_update):
    receipt = signed_update[3]
    data = json.loads(receipt.read_text())
    data["request"]["source_active_sha256"] = "changed"
    receipt.write_text(json.dumps(data))
    with pytest.raises(nu.UpdateError, match="source"):
        signed_prepare(signed_update)


def test_signed_evidence_change_after_approval_blocks_apply(signed_update):
    manager, _, _, receipt, _ = signed_update
    signed_prepare(signed_update)
    path = manager.registry / "updates/5-probe--signed.prepared.json"
    manager.approve("probe", "signed", prepared_sha256=nh._digest(path))
    receipt.write_text("changed")
    with pytest.raises(nu.UpdateError, match="evidence changed"):
        manager.apply("probe", "signed")
    assert manager.packages.calls == 0
    assert not (manager.registry / "updates/5-probe--signed.apply-intent.json").exists()


@pytest.mark.parametrize("field,value", [("state", "failed"), ("activated", True)])
def test_signed_prepare_rejects_nonprepared_receipt(signed_update, field, value):
    receipt = signed_update[3]
    data = json.loads(receipt.read_text())
    data[field] = value
    receipt.write_text(json.dumps(data))
    with pytest.raises(nu.UpdateError, match="authenticated release"):
        signed_prepare(signed_update)


def test_signed_prepare_rejects_wrong_release_digest(signed_update):
    receipt = signed_update[3]
    data = json.loads(receipt.read_text())
    data["request"]["release_sha256"] = "0" * 64
    receipt.write_text(json.dumps(data))
    with pytest.raises(nu.UpdateError, match="authenticated release"):
        signed_prepare(signed_update)
