import json
import os
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

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


@pytest.mark.parametrize("failure_stage", ["file", "directory"])
def test_apply_intent_fsync_failure_never_calls_package_manager(update, monkeypatch, failure_stage):
    manager, packages, helper, _ = update
    # Exercise the Debian-specific preparation branch without invoking dpkg or
    # copying a runtime; this fixture does not establish snapshot durability.
    debian = nu.Debian()
    prepared = []
    monkeypatch.setattr(debian, "prepare_execution", lambda stem: prepared.append(stem) or {
        "runtime_snapshot_sha256": "fixture-snapshot"
    })
    monkeypatch.setattr(debian, "metadata", packages.metadata)
    monkeypatch.setattr(debian, "installed", packages.installed)
    monkeypatch.setattr(debian, "apply", packages.apply)
    manager.packages = debian
    original_atomic = nu._atomic
    original_fsync = os.fsync
    armed = False

    def inject_atomic(path, value):
        nonlocal armed
        assert path.name.endswith(".apply-intent.json")
        armed = True
        try:
            return original_atomic(path, value)
        finally:
            armed = False

    def fail_fsync(fd):
        if armed and stat.S_ISDIR(os.fstat(fd).st_mode) == (failure_stage == "directory"):
            raise OSError("injected apply intent fsync failure")
        return original_fsync(fd)

    monkeypatch.setattr(nu, "_atomic", inject_atomic)
    monkeypatch.setattr(nh.os, "fsync", fail_fsync)
    old_active = (helper.root / "active.json").read_bytes()
    with pytest.raises(OSError, match="injected apply intent fsync failure"):
        manager.apply("probe", "one")
    intent = manager.registry / "updates/5-probe--one.apply-intent.json"
    assert len(prepared) == 1 and packages.calls == 0
    assert intent.exists() is (failure_stage == "directory")
    assert (helper.root / "active.json").read_bytes() == old_active
    if intent.exists():
        observation = manager.apply("probe", "one")
        assert observation["apply_intent"] is not None and observation["outcome"] is None
        assert len(prepared) == 1 and packages.calls == 0


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_apply_process_exit_after_durable_intent_does_not_replay(update, monkeypatch):
    if os.environ.get("DEVICE_CRASH_TEST_CHILD") != "1":
        env = {**os.environ, "DEVICE_CRASH_TEST_CHILD": "1"}
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"{__file__}::test_apply_process_exit_after_durable_intent_does_not_replay"],
            check=True, timeout=30, env=env,
        )
        return
    manager, packages, _, package = update
    manager.prepare("probe", "two", package, sha256=nh._digest(package), version="2")
    second = manager.registry / "updates/5-probe--two.prepared.json"
    manager.approve("probe", "two", prepared_sha256=nh._digest(second))
    parent_pid = os.getpid()
    original_atomic = nu._atomic
    package_marker = manager.registry / "unexpected-package-apply"

    def save_and_exit(path, value):
        original_atomic(path, value)
        if os.getpid() != parent_pid and path.name.endswith(".apply-intent.json"):
            os._exit(73)

    def unexpected_apply(*args, **kwargs):
        package_marker.write_text("called")
        raise AssertionError("package manager must not execute")

    monkeypatch.setattr(nu, "_atomic", save_and_exit)
    monkeypatch.setattr(packages, "apply", unexpected_apply)
    child = os.fork()
    if child == 0:
        try:
            manager.apply("probe", "one")
        finally:
            os._exit(74)
    _, status = os.waitpid(child, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 73
    assert (manager.registry / "updates/5-probe--one.apply-intent.json").exists()
    assert not package_marker.exists()
    observation = manager.apply("probe", "one")
    assert observation["apply_intent"] is not None and observation["outcome"] is None
    with pytest.raises(nu.UpdateError, match="existing package"):
        manager.apply("probe", "two")
    assert not package_marker.exists()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_apply_process_exit_after_package_action_begins_stays_unknown(update, monkeypatch):
    if os.environ.get("DEVICE_CRASH_TEST_CHILD") != "1":
        env = {**os.environ, "DEVICE_CRASH_TEST_CHILD": "1"}
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"{__file__}::test_apply_process_exit_after_package_action_begins_stays_unknown"],
            check=True, timeout=30, env=env,
        )
        return
    manager, packages, _, package = update
    manager.prepare("probe", "two", package, sha256=nh._digest(package), version="2")
    second = manager.registry / "updates/5-probe--two.prepared.json"
    manager.approve("probe", "two", prepared_sha256=nh._digest(second))
    marker = manager.registry / "package-started-test-marker"
    parent_pid = os.getpid()

    def apply_then_exit(*args, **kwargs):
        if os.getpid() == parent_pid:
            raise AssertionError("unknown operation must not replay")
        marker.write_text("started")
        os._exit(73)

    monkeypatch.setattr(packages, "apply", apply_then_exit)
    child = os.fork()
    if child == 0:
        try:
            manager.apply("probe", "one")
        finally:
            os._exit(74)
    _, status = os.waitpid(child, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 73
    assert marker.read_text() == "started"
    stem = manager.registry / "updates/5-probe--one"
    assert stem.with_suffix(".apply-intent.json").exists()
    assert not stem.with_suffix(".outcome.json").exists()
    assert not stem.with_suffix(".sender.json").exists()
    observation = manager.apply("probe", "one")
    assert observation["apply_intent"] is not None and observation["outcome"] is None
    with pytest.raises(nu.UpdateError, match="existing package"):
        manager.apply("probe", "two")
    assert marker.read_text() == "started"


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


@pytest.mark.parametrize("bad_code", ["missing", None, True, "0", 1.5, [], {}])
def test_malformed_package_sender_returncode_blocks_recovery(update, bad_code):
    manager, packages, _, package = update
    packages.mode = "failure"
    manager.apply("probe", "one")
    stem = manager.registry / "updates/5-probe--one"
    path = stem.with_suffix(".sender.json")
    receipt = json.loads(path.read_text())
    if bad_code == "missing":
        receipt.pop("returncode")
    else:
        receipt["returncode"] = bad_code
    path.write_text(json.dumps(receipt))
    intent = stem.with_suffix(".apply-intent.json").read_bytes()
    with pytest.raises(nu.UpdateError, match="matching package sender completion"):
        manager.recover("probe", "one")
    assert not stem.with_suffix(".recovery.json").exists()
    assert stem.with_suffix(".apply-intent.json").read_bytes() == intent
    manager.prepare("probe", "two", package, sha256=nh._digest(package), version="2")
    prepared = manager.registry / "updates/5-probe--two.prepared.json"
    manager.approve("probe", "two", prepared_sha256=nh._digest(prepared))
    with pytest.raises(nu.UpdateError, match="existing package"):
        manager.apply("probe", "two")
    assert packages.calls == 1


@pytest.mark.parametrize("value", [None, [], "sender_reaped", 1])
def test_non_object_package_sender_receipt_blocks_recovery(update, value):
    manager, packages, _, _ = update
    packages.mode = "failure"
    manager.apply("probe", "one")
    stem = manager.registry / "updates/5-probe--one"
    stem.with_suffix(".sender.json").write_text(json.dumps(value))
    with pytest.raises(nu.UpdateError, match="matching package sender completion"):
        manager.recover("probe", "one")
    assert stem.with_suffix(".apply-intent.json").exists()
    assert not stem.with_suffix(".recovery.json").exists()
    assert packages.calls == 1


@pytest.mark.parametrize("returncode", [-9, 1, 124])
def test_nonzero_package_sender_receipt_uses_observed_package_state(update, returncode):
    manager, packages, _, _ = update
    packages.mode = "failure"
    manager.apply("probe", "one")
    path = manager.registry / "updates/5-probe--one.sender.json"
    receipt = json.loads(path.read_text())
    receipt["returncode"] = returncode
    path.write_text(json.dumps(receipt))
    result = manager.recover("probe", "one")
    assert result["recovery"]["state"] == "source_package_observed_recovery_required"
    assert result["recovery"]["reapplied"] is False and packages.calls == 1


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


def test_started_result_without_proven_sender_tree_blocks_package_update(update):
    manager, packages, _, _ = update
    intent = {"action": "start", "execution_barrier": 2}
    path = manager.registry / "op-probe-start.json"
    result = manager.registry / "result-probe-start.json"
    path.write_text(json.dumps(intent))
    result.write_text(json.dumps({"state": "started"}))
    with pytest.raises(nu.UpdateError, match="sender process tree completion unproven"):
        manager.apply("probe", "one")
    assert packages.calls == 0
    assert not (manager.registry / "updates/5-probe--one.apply-intent.json").exists()


def test_unknown_start_blocks_update_even_when_service_is_inactive(update):
    manager, packages, _, _ = update
    (manager.registry / "op-probe-start.json").write_text('{"action":"start"}')
    with pytest.raises(nu.UpdateError, match="unconfirmed start"):
        manager.apply("probe", "one")
    assert packages.calls == 0


@pytest.mark.parametrize("state", [None, "failed", "unexpected"])
def test_invalid_start_result_blocks_package_update(update, state):
    manager, packages, _, _ = update
    (manager.registry / "op-probe-start.json").write_text('{"action":"start"}')
    (manager.registry / "result-probe-start.json").write_text(json.dumps({"state": state}))
    with pytest.raises(nu.UpdateError, match="invalid start result"):
        manager.apply("probe", "one")
    assert packages.calls == 0


@pytest.mark.parametrize("replayed", [None, True])
def test_incomplete_start_recovery_blocks_package_update(update, replayed):
    manager, packages, _, _ = update
    intent = {"action": "start"}
    (manager.registry / "op-probe-start.json").write_text(json.dumps(intent))
    recovery = {"state": "confirmed_stopped", "intent": intent}
    if replayed is not None:
        recovery["start_replayed"] = replayed
    (manager.registry / "recovery-probe-start.json").write_text(json.dumps(recovery))
    with pytest.raises(nu.UpdateError, match="unconfirmed start"):
        manager.apply("probe", "one")
    assert packages.calls == 0


@pytest.mark.parametrize("barrier", [None, 1])
def test_legacy_start_recovery_blocks_package_update(update, barrier):
    manager, packages, _, _ = update
    intent = {"action": "start"}
    if barrier is not None:
        intent["execution_barrier"] = barrier
    (manager.registry / "op-probe-start.json").write_text(json.dumps(intent))
    recovery = manager.registry / "recovery-probe-start.json"
    recovery.write_text(json.dumps({"state": "confirmed_stopped", "intent": intent, "start_replayed": False}))
    before = recovery.read_bytes()
    with pytest.raises(nu.UpdateError, match="unconfirmed start"):
        manager.apply("probe", "one")
    assert recovery.read_bytes() == before
    assert packages.calls == 0
    assert not (manager.registry / "updates/5-probe--one.apply-intent.json").exists()


def test_direct_sender_recovery_cannot_authorize_package_update(update):
    manager, packages, _, _ = update
    intent = {"action": "start", "execution_barrier": 2}
    (manager.registry / "op-probe-start.json").write_text(json.dumps(intent))
    recovery = manager.registry / "recovery-probe-start.json"
    recovery.write_text(json.dumps({"state": "confirmed_stopped", "intent": intent, "start_replayed": False}))
    before = recovery.read_bytes()
    with pytest.raises(nu.UpdateError, match="unconfirmed start"):
        manager.apply("probe", "one")
    assert recovery.read_bytes() == before
    assert packages.calls == 0
    assert not (manager.registry / "updates/5-probe--one.apply-intent.json").exists()


def test_valid_start_recovery_allows_stopped_package_update(update, monkeypatch):
    # Historical inner path only; production sender-tree gate remains closed.
    monkeypatch.setattr(nh, "_require_proven_start_sender_tree", lambda: None)
    manager, packages, _, _ = update
    intent = {"action": "start", "execution_barrier": 2}
    (manager.registry / "op-probe-start.json").write_text(json.dumps(intent))
    (manager.registry / "recovery-probe-start.json").write_text(json.dumps({
        "state": "confirmed_stopped", "intent": intent, "start_replayed": False,
    }))
    manager.apply("probe", "one")
    assert packages.calls == 1


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


def test_oversized_runtime_receipt_does_not_prepare(signed_update):
    manager, _, _, receipt, _ = signed_update
    receipt.write_bytes(b"{}" + b" " * 256000)
    with pytest.raises(nu.UpdateError, match="receipt exceeds budget"):
        signed_prepare(signed_update)
    assert not (manager.registry / "updates/5-probe--signed.prepared.json").exists()


def test_runtime_receipt_change_after_parse_does_not_prepare(signed_update, monkeypatch):
    manager, _, _, receipt, _ = signed_update
    original_json = nu.nc._json
    original_bytes = receipt.read_bytes()

    def change_after_parse(data):
        result = original_json(data)
        if data == original_bytes:
            receipt.write_bytes(data + b" ")
        return result

    monkeypatch.setattr(nu.nc, "_json", change_after_parse)
    with pytest.raises(nu.UpdateError, match="evidence changed"):
        signed_prepare(signed_update)
    assert not (manager.registry / "updates/5-probe--signed.prepared.json").exists()


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


def test_independent_runtime_failure_precedes_apply_intent(update):
    manager, packages, _helper, _package = update
    registry = manager.registry

    class SnapshotFailure(nu.Debian):
        def metadata(self, path):
            return packages.metadata(path)

        def installed(self, name):
            return packages.installed(name)

        def prepare_execution(self, stem):
            raise nu.nb.BootstrapError("injected snapshot failure")

        def apply(self, *a, **kw):
            pytest.fail("must never execute package")

    manager.packages = SnapshotFailure()
    with pytest.raises(nu.nb.BootstrapError, match="snapshot failure"):
        manager.apply("probe", "one")
    assert not list((registry / "updates").glob("*.apply-intent.json"))


def test_snapshot_intent_recovery_checks_target_and_receipt(update, monkeypatch):
    manager, packages, _helper, _package = update
    manager.apply("probe", "one")
    stem = manager.registry / "updates/5-probe--one"
    snapshot = stem.with_suffix(".bootstrap")
    snapshot.mkdir()
    (snapshot / "snapshot.json").write_text("fixture snapshot")
    monkeypatch.setattr(nu.nb, "validate_snapshot", lambda directory: {})
    intent = json.loads(stem.with_suffix(".apply-intent.json").read_bytes())
    prepared = json.loads(stem.with_suffix(".prepared.json").read_bytes())
    intent.update(
        {
            "runtime_snapshot_sha256": nh._digest(snapshot / "snapshot.json"),
            "target_package": {
                "name": prepared["metadata"]["Package"],
                "version": prepared["metadata"]["Version"],
                "architecture": prepared["metadata"]["Architecture"],
            },
        }
    )
    stem.with_suffix(".apply-intent.json").write_text(json.dumps(intent))
    sender = json.loads(stem.with_suffix(".sender.json").read_bytes())
    sender["intent"] = intent
    stem.with_suffix(".sender.json").write_text(json.dumps(sender))
    baseline = {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}
    with pytest.raises(nu.UpdateError, match="process tree completion unproven"):
        manager.recover("probe", "one")
    assert {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()} == baseline
    assert packages.calls == 1 and not stem.with_suffix(".recovery.json").exists()
    # Preserve coverage of prior validation only via a test-only substitute.
    monkeypatch.setattr(nu, "_require_proven_package_tree_reaped", lambda: None)
    assert (
        manager.recover("probe", "one")["recovery"]["state"] == "package_applied_pending_activation"
    )
    assert packages.calls == 1
    intent["target_package"]["version"] = "wrong"
    stem.with_suffix(".apply-intent.json").write_text(json.dumps(intent))
    with pytest.raises(nu.UpdateError, match="intent differs"):
        manager.recover("probe", "one")


def test_queued_job_blocks_direct_apply_and_start(update):
    manager, packages, _helper, _package = update
    path = manager.registry / "updates/5-probe--one.job-intent.json"
    path.write_text("{}")
    with pytest.raises(nu.UpdateError, match="queued package job"):
        manager.apply("probe", "one")
    with pytest.raises(nh.HelperError, match="blocks start"):
        nh._assert_no_package_intent(manager.registry)
    assert packages.calls == 0


def test_existing_job_worker_refuses_new_apply_without_proven_fence(update, monkeypatch):
    import hashlib

    manager, packages, _helper, _package = update
    stem = manager.registry / "updates/5-probe--one"
    snapshot = stem.with_suffix(".bootstrap")
    snapshot.mkdir()
    (snapshot / "snapshot.json").write_text("snapshot")
    monkeypatch.setattr(nu.nb, "validate_snapshot", lambda directory: {})
    path = stem.with_suffix(".job-intent.json")
    path.write_text(json.dumps({
        "schema_version": 1, "state": "job_dispatch_intent", "close_protocol": "package-lease-v1",
        "unit": "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service",
        "instance_id": "probe", "operation_id": "one",
        "prepared_sha256": nh._digest(stem.with_suffix(".prepared.json")),
        "runtime_snapshot_sha256": nh._digest(snapshot / "snapshot.json"),
    }))
    before = {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}
    with pytest.raises(nu.UpdateError, match="job worker disabled: execution fence unproven"):
        manager.apply("probe", "one", job_intent_sha256=nh._digest(path))
    assert packages.calls == 0
    assert not stem.with_suffix(".apply-intent.json").exists()
    assert {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()} == before


def test_existing_apply_remains_observable_with_job_worker_gate(update):
    manager, packages, _helper, _package = update
    first = manager.apply("probe", "one")
    stem = manager.registry / "updates/5-probe--one"
    before = {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}
    observed = manager.apply("probe", "one", job_intent_sha256="fixture-digest")
    assert observed == first
    assert packages.calls == 1
    assert {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()} == before


def test_worker_rechecks_job_intent_and_source(update, monkeypatch):
    import hashlib

    # Exercise dormant validation after the real default-deny is tested separately.
    monkeypatch.setattr(nu, "_require_proven_job_execution_fence", lambda: None)
    manager, packages, helper, _package = update
    stem = manager.registry / "updates/5-probe--one"
    snapshot = stem.with_suffix(".bootstrap")
    snapshot.mkdir()
    (snapshot / "snapshot.json").write_text("snapshot")
    monkeypatch.setattr(nu.nb, "validate_snapshot", lambda directory: {})
    intent = {
        "schema_version": 1,
        "state": "job_dispatch_intent",
        "close_protocol": "package-lease-v1",
        "unit": "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service",
        "instance_id": "probe",
        "operation_id": "one",
        "prepared_sha256": nh._digest(stem.with_suffix(".prepared.json")),
        "runtime_snapshot_sha256": nh._digest(snapshot / "snapshot.json"),
    }
    path = stem.with_suffix(".job-intent.json")
    for key, bad in (("schema_version", True), ("close_protocol", "other"), ("state", "other"),
                     ("unit", "wrong"), ("instance_id", "other"), ("operation_id", "other"), ("extra", True)):
        changed = {**intent, key: bad}
        path.write_text(json.dumps(changed))
        with pytest.raises(nu.UpdateError, match="job intent differs"):
            manager.apply("probe", "one", job_intent_sha256=nh._digest(path))
        assert packages.calls == 0 and not stem.with_suffix(".apply-intent.json").exists()
    for changed in ({k: v for k, v in intent.items() if k != "close_protocol"}, [], None,
                    {**intent, "prepared_sha256": "wrong"}, {**intent, "runtime_snapshot_sha256": "wrong"}):
        path.write_text(json.dumps(changed))
        with pytest.raises(nu.UpdateError, match="job intent differs"):
            manager.apply("probe", "one", job_intent_sha256=nh._digest(path))
        assert packages.calls == 0 and not stem.with_suffix(".apply-intent.json").exists()
    path.write_text(json.dumps(intent))
    digest = nh._digest(path)
    with pytest.raises(nu.UpdateError, match="job intent changed"):
        manager.apply("probe", "one", job_intent_sha256="wrong")
    assert packages.calls == 0
    # Existing source checks run again at worker execution, not just dispatch.
    active = helper.root / "active.json"
    before = active.read_bytes()
    active.write_text('{"release":"changed"}')
    with pytest.raises(nu.UpdateError, match="source active"):
        manager.apply("probe", "one", job_intent_sha256=digest)
    active.write_bytes(before)
    assert (
        manager.apply("probe", "one", job_intent_sha256=digest)["outcome"]["state"]
        == "package_applied_pending_activation"
    )
    assert packages.calls == 1


def test_explicit_job_recovery_retains_fence_and_never_replays(update, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from device_runtime import native_update_job as job

    manager, packages, _helper, _package = update
    manager.apply("probe", "one")
    stem = manager.registry / "updates/5-probe--one"
    snapshot = stem.with_suffix(".bootstrap")
    snapshot.mkdir()
    (snapshot / "snapshot.json").write_text("snapshot")
    monkeypatch.setattr(nu.nb, "validate_snapshot", lambda *a: {})
    apply = json.loads(stem.with_suffix(".apply-intent.json").read_bytes())
    apply.update(
        {
            "runtime_snapshot_sha256": nh._digest(snapshot / "snapshot.json"),
            "target_package": {
                "name": "ai-device-native-client",
                "version": "2",
                "architecture": "amd64",
            },
        }
    )
    stem.with_suffix(".apply-intent.json").write_text(json.dumps(apply))
    sender = json.loads(stem.with_suffix(".sender.json").read_bytes())
    sender["intent"] = apply
    stem.with_suffix(".sender.json").write_text(json.dumps(sender))
    outcome = json.loads(stem.with_suffix(".outcome.json").read_bytes())
    outcome["intent"] = apply
    stem.with_suffix(".outcome.json").write_text(json.dumps(outcome))
    intent = {
        "schema_version": 1,
        "state": "job_dispatch_intent",
        "close_protocol": "package-lease-v1",
        "unit": "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service",
        "instance_id": "probe",
        "operation_id": "one",
        "prepared_sha256": apply["prepared_sha256"],
        "runtime_snapshot_sha256": apply["runtime_snapshot_sha256"],
    }
    stem.with_suffix(".job-intent.json").write_text(json.dumps(intent))

    # Production job recovery is closed at both the public and lower-level
    # entry, before observation, transaction writes, or package activity.
    baseline = {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}
    observed = []
    with pytest.raises(nu.UpdateError, match="job recovery disabled"):
        job.recover_job(manager, "probe", "one", runner=lambda *a, **k: observed.append(a))
    with pytest.raises(nu.UpdateError, match="job recovery disabled"):
        manager.recover("probe", "one", job_intent_sha256=nh._digest(stem.with_suffix(".job-intent.json")))
    assert not observed and packages.calls == 1
    assert {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()} == baseline
    # The remaining assertions exercise the old reconciliation logic only as a
    # fixture; this substitute is never a production authorization.
    monkeypatch.setattr(nu, "_require_proven_job_recovery_fence", lambda: None)
    # A legacy direct-child package receipt cannot prove tree completion.
    with pytest.raises(nu.UpdateError, match="process tree completion unproven"):
        manager.recover("probe", "one", job_intent_sha256=nh._digest(stem.with_suffix(".job-intent.json")))
    assert not stem.with_suffix(".recovery.json").exists() and packages.calls == 1
    assert {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()} == baseline
    # The historical logic below is exercised with both test-only substitutes.
    monkeypatch.setattr(nu, "_require_proven_package_tree_reaped", lambda: None)

    # General update recovery must not bypass job-specific observation/binding.
    before_direct = {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}
    with pytest.raises(nu.UpdateError, match="job recovery requires explicit job binding"):
        manager.recover("probe", "one")
    assert {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()} == before_direct
    assert packages.calls == 1
    job_intent_path = stem.with_suffix(".job-intent.json")
    job_intent_path.unlink()  # Isolated fixture only; retain its exact content in intent.
    try:
        for suffix in (".job-intent.json", ".job-recovery.json"):
            marker = stem.with_suffix(suffix)
            marker.symlink_to(stem.parent / "missing-fixture-target")
            try:
                with pytest.raises(nu.UpdateError, match="job recovery requires explicit job binding"):
                    manager.recover("probe", "one")
                assert not stem.with_suffix(".recovery.json").exists()
                assert marker.is_symlink() and packages.calls == 1
            finally:
                marker.unlink()
    finally:
        job_intent_path.write_text(json.dumps(intent))

    for key, bad in (("schema_version", True), ("close_protocol", "unknown"),
                     ("state", "unexpected"), ("unit", "wrong.service"),
                     ("runtime_snapshot_sha256", "wrong"), ("extra", True)):
        changed = dict(intent)
        changed[key] = bad
        stem.with_suffix(".job-intent.json").write_text(json.dumps(changed))
        with pytest.raises(nu.UpdateError, match="job recovery intent differs"):
            manager.recover("probe", "one", job_intent_sha256=nh._digest(stem.with_suffix(".job-intent.json")))
        assert not stem.with_suffix(".recovery.json").exists() and packages.calls == 1
    for changed in ({k: v for k, v in intent.items() if k != "close_protocol"}, [], None):
        stem.with_suffix(".job-intent.json").write_text(json.dumps(changed))
        with pytest.raises(nu.UpdateError, match="job recovery intent differs"):
            manager.recover("probe", "one", job_intent_sha256=nh._digest(stem.with_suffix(".job-intent.json")))
        assert not stem.with_suffix(".recovery.json").exists() and packages.calls == 1
    stem.with_suffix(".job-intent.json").write_text(json.dumps(intent))
    (snapshot / "snapshot.json").write_text("drift")
    with pytest.raises(nu.UpdateError, match="job recovery intent differs"):
        manager.recover("probe", "one", job_intent_sha256=nh._digest(stem.with_suffix(".job-intent.json")))
    assert not stem.with_suffix(".recovery.json").exists() and packages.calls == 1
    (snapshot / "snapshot.json").write_text("snapshot")

    def runner(*a, **kw):
        return SimpleNamespace(stdout="LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\nJob=\n")

    def retained(*a, **kw):
        return SimpleNamespace(
            stdout="LoadState=loaded\nActiveState=active\nSubState=exited\nResult=success\nMainPID=0\nJob=0\n"
        )

    invalid_observations = [
        (
            "LoadState=loaded\nActiveState=active\nActiveState=inactive\n"
            "SubState=exited\nResult=success\nMainPID=0\nJob=0\n"
        ),
        "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\n",
        "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\nJob=0\ninvalid\n",
        "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\nJob=0\nExtra=x\n",
        "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=" + "x" * 16384 + "\nMainPID=0\nJob=0\n",
    ]
    for output in invalid_observations:
        with pytest.raises(job.JobError, match="completed job"):
            job.recover_job(manager, "probe", "one", runner=lambda *a, output=output, **kw: SimpleNamespace(stdout=output))
        assert not stem.with_suffix(".job-recovery.json").exists()
        assert not stem.with_suffix(".recovery.json").exists()
        assert packages.calls == 1

    # An orphan job-recovery receipt cannot justify synthesizing its missing
    # lower-level recovery evidence, even if the job is observed terminal.
    orphan = stem.with_suffix(".job-recovery.json")
    orphan.write_text(json.dumps({"retry_allowed": 0, "fence_retained": 1}))
    before_orphan = {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}
    with pytest.raises(nu.UpdateError, match="job recovery evidence without underlying recovery"):
        job.recover_job(manager, "probe", "one", runner=retained)
    assert {p.name: p.read_bytes() for p in stem.parent.iterdir() if p.is_file()} == before_orphan
    assert packages.calls == 1
    orphan.unlink()  # This file is owned by this isolated test fixture only.

    first = job.recover_job(manager, "probe", "one", runner=retained)
    assert first["state"] == "job_reconciled_without_replay"
    assert first["fence_retained"] and not first["retry_allowed"]
    assert job.recover_job(manager, "probe", "one", runner=runner) == first
    assert packages.calls == 1
    receipt_path = stem.with_suffix(".job-recovery.json")
    original_receipt = receipt_path.read_bytes()
    for key, bad in (("retry_allowed", 0), ("fence_retained", 1)):
        drifted = {**first, key: bad}
        receipt_path.write_text(json.dumps(drifted))
        before_files = {path.name: path.read_bytes() for path in stem.parent.iterdir() if path.is_file()}
        with pytest.raises(nu.UpdateError, match="job recovery evidence changed"):
            job.recover_job(manager, "probe", "one", runner=runner)
        assert {path.name: path.read_bytes() for path in stem.parent.iterdir() if path.is_file()} == before_files
        assert packages.calls == 1
        receipt_path.write_bytes(original_receipt)
    assert job.recover_job(manager, "probe", "one", runner=runner) == first
    before = {suffix: stem.with_suffix(suffix).read_bytes() for suffix in (
        ".job-intent.json", ".apply-intent.json", ".sender.json", ".recovery.json", ".job-recovery.json"
    )}
    # A late worker with the same durable intent observes, never reruns the package.
    late = manager.apply("probe", "one", job_intent_sha256=nh._digest(stem.with_suffix(".job-intent.json")))
    assert late["recovery"]["reapplied"] is False and packages.calls == 1
    assert before == {suffix: stem.with_suffix(suffix).read_bytes() for suffix in before}
    with pytest.raises(nh.HelperError, match="blocks start"):
        nh._assert_no_package_intent(manager.registry)

    def busy(*a, **kw):
        return SimpleNamespace(stdout="LoadState=loaded\nActiveState=active\nMainPID=123\nJob=0\n")

    with pytest.raises(job.JobError, match="completed job"):
        job.recover_job(manager, "probe", "one", runner=busy)

    def missing(*a, **kw):
        return SimpleNamespace(
            stdout="LoadState=not-found\nActiveState=inactive\nMainPID=0\nJob=\n"
        )

    with pytest.raises(job.JobError, match="completed job"):
        job.recover_job(manager, "probe", "one", runner=missing)

    def active_running(*a, **kw):
        return SimpleNamespace(
            stdout="LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\nMainPID=0\nJob=0\n"
        )

    with pytest.raises(job.JobError, match="completed job"):
        job.recover_job(manager, "probe", "one", runner=active_running)


def test_close_before_apply_fences_delayed_worker(update, monkeypatch):
    import hashlib

    from device_runtime import native_update_job as job

    manager, packages, _, _ = update
    stem = manager.registry / "updates/5-probe--one"
    snapshot = stem.with_suffix(".bootstrap")
    snapshot.mkdir()
    (snapshot / "snapshot.json").write_text("snapshot")
    monkeypatch.setattr(job.bootstrap, "validate_snapshot", lambda *a: {})
    original_digest = nh._digest

    def digest(path):
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py"):
            return "same-worker"
        return original_digest(path)

    monkeypatch.setattr(nh, "_digest", digest)
    intent = {
        "schema_version": 1,
        "state": "job_dispatch_intent",
        "close_protocol": "package-lease-v1",
        "instance_id": "probe",
        "operation_id": "one",
        "unit": "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service",
        "prepared_sha256": nh._digest(stem.with_suffix(".prepared.json")),
        "runtime_snapshot_sha256": nh._digest(snapshot / "snapshot.json"),
    }
    path = stem.with_suffix(".job-intent.json")
    path.write_text(json.dumps(intent))
    with manager._apply_lease(manager.registry / "package-manager"):
        pass
    closed = job.close_before_apply(manager, "probe", "one")
    assert closed["state"] == "closed_before_apply"
    assert closed["fence_retained"] and not closed["retry_allowed"]
    assert job.close_before_apply(manager, "probe", "one") == closed
    with pytest.raises(nu.UpdateError, match="closed before execution"):
        manager.apply("probe", "one", job_intent_sha256=nh._digest(path))
    assert packages.calls == 0 and not stem.with_suffix(".apply-intent.json").exists()
    with pytest.raises(nh.HelperError, match="blocks start"):
        nh._assert_no_package_intent(manager.registry)
    intent.pop("close_protocol")
    path.write_text(json.dumps(intent))
    with pytest.raises(job.JobError, match="intent or approval"):
        job.close_before_apply(manager, "probe", "one")


def test_close_refuses_busy_package_lease_and_prior_apply(update, monkeypatch):
    import hashlib

    from device_runtime import native_update_job as job

    manager, packages, _, _ = update
    stem = manager.registry / "updates/5-probe--one"
    snapshot = stem.with_suffix(".bootstrap")
    snapshot.mkdir()
    (snapshot / "snapshot.json").write_text("snapshot")
    monkeypatch.setattr(job.bootstrap, "validate_snapshot", lambda *a: {})
    real_digest = nh._digest
    monkeypatch.setattr(
        nh,
        "_digest",
        lambda path: (
            "same-worker"
            if path.name in ("native_update.py", "native_update_job.py", "native_helper.py")
            else real_digest(path)
        ),
    )
    intent = {
        "schema_version": 1,
        "state": "job_dispatch_intent",
        "close_protocol": "package-lease-v1",
        "instance_id": "probe",
        "operation_id": "one",
        "unit": "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service",
        "prepared_sha256": nh._digest(stem.with_suffix(".prepared.json")),
        "runtime_snapshot_sha256": nh._digest(snapshot / "snapshot.json"),
    }
    stem.with_suffix(".job-intent.json").write_text(json.dumps(intent))
    with (
        manager._apply_lease(manager.registry / "package-manager"),
        pytest.raises(nu.UpdateError, match="still executing"),
    ):
        job.close_before_apply(manager, "probe", "one")
    assert not stem.with_suffix(".job-closed.json").exists()
    stem.with_suffix(".apply-intent.json").write_text("{}")
    with pytest.raises(job.JobError, match="apply evidence exists"):
        job.close_before_apply(manager, "probe", "one")
    assert packages.calls == 0 and not stem.with_suffix(".job-closed.json").exists()


def test_retirement_default_denied_without_writes(update):
    from device_runtime import native_update_job as job

    manager, packages, _, _ = update
    directory = manager.registry / "updates"
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    with pytest.raises(job.JobError, match="job retirement disabled: close durability unproven"):
        job.retire_closed_job(manager, "probe", "one")
    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == before
    assert packages.calls == 0


def test_uncertain_close_and_retirement_cannot_release_job_fence(update, monkeypatch):
    import hashlib
    import stat

    from device_runtime import native_update_job as job

    manager, packages, _, package = update
    stem = manager.registry / "updates/5-probe--one"
    snapshot = stem.with_suffix(".bootstrap")
    snapshot.mkdir()
    (snapshot / "snapshot.json").write_text("snapshot")
    monkeypatch.setattr(job.bootstrap, "validate_snapshot", lambda *a: {})
    real_digest = nh._digest
    monkeypatch.setattr(nh, "_digest", lambda path: (
        "same-worker" if path.name in ("native_update.py", "native_update_job.py", "native_helper.py")
        else real_digest(path)
    ))
    intent = {
        "schema_version": 1, "state": "job_dispatch_intent", "close_protocol": "package-lease-v1",
        "instance_id": "probe", "operation_id": "one",
        "unit": "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service",
        "prepared_sha256": nh._digest(stem.with_suffix(".prepared.json")),
        "runtime_snapshot_sha256": nh._digest(snapshot / "snapshot.json"),
    }
    path = stem.with_suffix(".job-intent.json")
    path.write_text(json.dumps(intent))
    with manager._apply_lease(manager.registry / "package-manager"):
        pass
    real_fsync, real_link = os.fsync, os.link
    linked = []

    def marked_link(source, destination, *args, **kwargs):
        result = real_link(source, destination, *args, **kwargs)
        if str(destination).endswith((".job-closed.json", ".job-retired.json")):
            linked.append(str(destination))
        return result

    def fail_directory(fd):
        if linked and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected directory fsync failure")
        return real_fsync(fd)

    with monkeypatch.context() as injected:
        injected.setattr(nh.os, "link", marked_link)
        injected.setattr(nh.os, "fsync", fail_directory)
        with pytest.raises(OSError, match="injected directory"):
            job.close_before_apply(manager, "probe", "one")
    assert stem.with_suffix(".job-closed.json").exists()
    # Reach the old publication path only to reproduce the failure; production
    # retirement remains closed and the reader must retain the fence regardless.
    monkeypatch.setattr(job, "_require_proven_retirement_fence", lambda: None)
    linked.clear()
    with monkeypatch.context() as injected:
        injected.setattr(nh.os, "link", marked_link)
        injected.setattr(nh.os, "fsync", fail_directory)
        with pytest.raises(OSError, match="injected directory"):
            job.retire_closed_job(manager, "probe", "one")
    assert stem.with_suffix(".job-retired.json").exists()
    assert nh._job_retired(path)  # Structurally valid is not durable proof.
    assert path in nh._pending_package_jobs(stem.parent)
    with pytest.raises(nh.HelperError, match="blocks start"):
        nh._assert_no_package_intent(manager.registry)
    original_evidence = (path.read_bytes(), stem.with_suffix(".job-closed.json").read_bytes(),
                         stem.with_suffix(".job-retired.json").read_bytes())
    manager.prepare("probe", "two", package, sha256=nh._digest(package), version="2")
    new = manager.registry / "updates/5-probe--two"
    manager.approve("probe", "two", prepared_sha256=nh._digest(new.with_suffix(".prepared.json")))
    with pytest.raises(nu.UpdateError, match="queued package job"):
        manager.apply("probe", "two")
    assert not new.with_suffix(".apply-intent.json").exists()
    assert original_evidence == (path.read_bytes(), stem.with_suffix(".job-closed.json").read_bytes(),
                                 stem.with_suffix(".job-retired.json").read_bytes())
    assert packages.calls == 0


def test_retired_job_allows_new_id_but_never_replays_old(update, monkeypatch):
    import hashlib

    from device_runtime import native_update_job as job

    # Historical mechanics only; production retirement and fence release remain disabled.
    monkeypatch.setattr(job, "_require_proven_retirement_fence", lambda: None)
    monkeypatch.setattr(nh, "_pending_package_jobs", lambda directory: [
        path for path in Path(directory).glob("*.job-intent.json") if not nh._job_retired(path)
    ])
    manager, packages, _, package = update
    stem = manager.registry / "updates/5-probe--one"
    snapshot = stem.with_suffix(".bootstrap")
    snapshot.mkdir()
    (snapshot / "snapshot.json").write_text("snapshot")
    monkeypatch.setattr(job.bootstrap, "validate_snapshot", lambda *a: {})
    original_digest = nh._digest

    def digest(path):
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py"):
            return "same-worker"
        return original_digest(path)

    monkeypatch.setattr(nh, "_digest", digest)
    intent = {
        "schema_version": 1,
        "state": "job_dispatch_intent",
        "close_protocol": "package-lease-v1",
        "instance_id": "probe",
        "operation_id": "one",
        "unit": "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service",
        "prepared_sha256": nh._digest(stem.with_suffix(".prepared.json")),
        "runtime_snapshot_sha256": nh._digest(snapshot / "snapshot.json"),
    }
    path = stem.with_suffix(".job-intent.json")
    path.write_text(json.dumps(intent))
    with manager._apply_lease(manager.registry / "package-manager"):
        pass
    for changed in ({**intent, "schema_version": True}, {**intent, "extra": "unexpected"}):
        path.write_text(json.dumps(changed))
        with pytest.raises(job.JobError, match="intent or approval"):
            job.close_before_apply(manager, "probe", "one")
        assert not stem.with_suffix(".job-closed.json").exists()
    path.write_text(json.dumps(intent))
    approved_path = stem.with_suffix(".approved.json")
    approved_original = approved_path.read_bytes()
    approved = json.loads(approved_original)
    approved["approved_by_uid"] = False
    approved_path.write_text(json.dumps(approved))
    with pytest.raises(job.JobError, match="intent or approval"):
        job.close_before_apply(manager, "probe", "one")
    assert not stem.with_suffix(".job-closed.json").exists()
    approved_path.write_bytes(approved_original)
    job.close_before_apply(manager, "probe", "one")
    close_path = stem.with_suffix(".job-closed.json")
    valid_close = close_path.read_bytes()
    malformed = json.loads(valid_close)
    malformed["retry_allowed"] = 0
    close_path.write_text(json.dumps(malformed))
    with pytest.raises(job.JobError, match="close evidence differs"):
        job.retire_closed_job(manager, "probe", "one")
    assert not stem.with_suffix(".job-retired.json").exists()
    close_path.write_bytes(valid_close)
    retired = job.retire_closed_job(manager, "probe", "one")
    assert job.retire_closed_job(manager, "probe", "one") == retired
    retired_path = stem.with_suffix(".job-retired.json")
    valid_retired = retired_path.read_bytes()
    malformed_retired = json.loads(valid_retired)
    malformed_retired["old_job_replay_allowed"] = 0
    retired_path.write_text(json.dumps(malformed_retired))
    with pytest.raises(job.JobError, match="retirement evidence changed"):
        job.retire_closed_job(manager, "probe", "one")
    assert json.loads(retired_path.read_bytes())["old_job_replay_allowed"] == 0
    retired_path.write_bytes(valid_retired)
    assert nh._job_retired(path)
    nh._assert_no_package_intent(manager.registry)
    other = stem.parent / "5-probe--other.job-intent.json"
    other.write_text("{}")
    with pytest.raises(nh.HelperError, match="blocks start"):
        nh._assert_no_package_intent(manager.registry)
    other.unlink()
    close_path = stem.with_suffix(".job-closed.json")
    old_close = close_path.read_bytes()
    close_path.write_text("{}")
    with pytest.raises(nh.HelperError, match="blocks start"):
        nh._assert_no_package_intent(manager.registry)
    close_path.write_bytes(old_close)
    nh._assert_no_package_intent(manager.registry)
    for kwargs in ({}, {"job_intent_sha256": nh._digest(path)}):
        with pytest.raises(nu.UpdateError, match="closed before execution"):
            manager.apply("probe", "one", **kwargs)
    manager.prepare("probe", "two", package, sha256=nh._digest(package), version="2")
    new = manager.registry / "updates/5-probe--two"
    manager.approve("probe", "two", prepared_sha256=nh._digest(new.with_suffix(".prepared.json")))
    result = manager.apply("probe", "two")
    assert result["outcome"]["state"] == "package_applied_pending_activation"
    assert packages.calls == 1
    with pytest.raises(nh.HelperError, match="blocks start"):
        nh._assert_no_package_intent(manager.registry)
    assert path.exists() and stem.with_suffix(".job-closed.json").exists()


def test_retirement_evidence_fail_closed(update, monkeypatch):
    manager, _, _, _ = update
    directory = manager.registry / "updates"
    path = directory / "5-probe--one.job-intent.json"
    path.write_text("{}")
    (directory / "5-probe--one.job-retired.json").write_text("{}")
    assert not nh._job_retired(path)
    path.write_text("[]")
    assert not nh._job_retired(path)
    with pytest.raises(nh.HelperError, match="blocks start"):
        nh._assert_no_package_intent(manager.registry)


@pytest.mark.parametrize("drift", ["schema", "retry", "fence", "replay"])
def test_retirement_fence_rejects_json_type_drift(update, drift):
    import hashlib

    manager, packages, _, _ = update
    stem = manager.registry / "updates/5-probe--one"
    path = stem.with_suffix(".job-intent.json")
    closed_path = stem.with_suffix(".job-closed.json")
    retired_path = stem.with_suffix(".job-retired.json")
    intent = {
        "schema_version": 1, "state": "job_dispatch_intent", "close_protocol": "package-lease-v1",
        "instance_id": "probe", "operation_id": "one",
        "unit": "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service",
        "prepared_sha256": "a" * 64, "runtime_snapshot_sha256": "b" * 64,
    }
    if drift == "schema":
        intent["schema_version"] = True
    path.write_text(json.dumps(intent))
    closed = {
        "state": "closed_before_apply", "job_intent_sha256": nh._digest(path),
        "retry_allowed": 0 if drift == "retry" else False,
        "fence_retained": 1 if drift == "fence" else True,
    }
    closed_path.write_text(json.dumps(closed))
    retired = {
        "state": "retired_before_apply", "job_intent_sha256": nh._digest(path),
        "closed_sha256": nh._digest(closed_path),
        "old_job_replay_allowed": 0 if drift == "replay" else False,
    }
    retired_path.write_text(json.dumps(retired))
    original = [item.read_bytes() for item in (path, closed_path, retired_path)]
    assert not nh._job_retired(path)
    with pytest.raises(nh.HelperError, match="blocks start"):
        nh._assert_no_package_intent(manager.registry)
    assert [item.read_bytes() for item in (path, closed_path, retired_path)] == original
    assert packages.calls == 0


@pytest.mark.parametrize("drift", ["missing_uid", "bool_uid", "extra", "not_object"])
def test_apply_rejects_malformed_admin_approval_before_action(update, drift):
    manager, packages, _, _ = update
    stem = manager.registry / "updates/5-probe--one"
    approved_path = stem.with_suffix(".approved.json")
    receipt = json.loads(approved_path.read_bytes())
    if drift == "missing_uid":
        del receipt["approved_by_uid"]
    elif drift == "bool_uid":
        receipt["approved_by_uid"] = False
    elif drift == "extra":
        receipt["unexpected"] = True
    else:
        receipt = [receipt]
    approved_path.write_text(json.dumps(receipt))
    original = approved_path.read_bytes()
    with pytest.raises(nu.UpdateError, match="accurate approval required"):
        manager.apply("probe", "one")
    assert packages.calls == 0
    assert not stem.with_suffix(".apply-intent.json").exists()
    assert approved_path.read_bytes() == original


def test_malformed_approval_after_apply_blocks_replay_observe_and_recovery(update):
    manager, packages, _, _ = update
    stem = manager.registry / "updates/5-probe--one"
    manager.apply("probe", "one")
    assert packages.calls == 1
    approved_path = stem.with_suffix(".approved.json")
    approval = json.loads(approved_path.read_bytes())
    approval["approved_by_uid"] = False
    approved_path.write_text(json.dumps(approval))
    evidence_paths = sorted(stem.parent.glob("5-probe--one.*"))
    evidence_before = {path.name: path.read_bytes() for path in evidence_paths if path.is_file()}
    for operation in (manager.apply, manager.reconcile, manager.recover):
        with pytest.raises(nu.UpdateError, match="accurate approval required"):
            operation("probe", "one")
    with pytest.raises(nu.UpdateError, match="approval conflicts"):
        manager.approve("probe", "one", prepared_sha256=nh._digest(stem.with_suffix(".prepared.json")))
    assert packages.calls == 1
    assert {path.name: path.read_bytes() for path in evidence_paths if path.is_file()} == evidence_before
    assert sorted(stem.parent.glob("5-probe--one.*")) == evidence_paths


@pytest.mark.parametrize("drift", ["missing", "extra", "instance", "operation", "unit", "prepared", "snapshot"])
def test_retirement_rejects_incomplete_job_identity(update, drift):
    import hashlib

    manager, packages, _, _ = update
    stem = manager.registry / "updates/5-probe--one"
    path = stem.with_suffix(".job-intent.json")
    close_path = stem.with_suffix(".job-closed.json")
    retire_path = stem.with_suffix(".job-retired.json")
    intent = {
        "schema_version": 1, "state": "job_dispatch_intent", "close_protocol": "package-lease-v1",
        "instance_id": "probe", "operation_id": "one",
        "unit": "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service",
        "prepared_sha256": "a" * 64, "runtime_snapshot_sha256": "b" * 64,
    }
    if drift == "missing":
        del intent["instance_id"]
    elif drift == "extra":
        intent["extra"] = True
    else:
        field = {"instance": "instance_id", "operation": "operation_id", "unit": "unit",
                 "prepared": "prepared_sha256", "snapshot": "runtime_snapshot_sha256"}[drift]
        intent[field] = "wrong"
    path.write_text(json.dumps(intent))
    close_path.write_text(json.dumps({
        "state": "closed_before_apply", "job_intent_sha256": nh._digest(path),
        "retry_allowed": False, "fence_retained": True,
    }))
    retire_path.write_text(json.dumps({
        "state": "retired_before_apply", "job_intent_sha256": nh._digest(path),
        "closed_sha256": nh._digest(close_path), "old_job_replay_allowed": False,
    }))
    before = [p.read_bytes() for p in (path, close_path, retire_path)]
    assert not nh._job_retired(path)
    with pytest.raises(nh.HelperError, match="blocks start"):
        nh._assert_no_package_intent(manager.registry)
    assert [p.read_bytes() for p in (path, close_path, retire_path)] == before
    assert packages.calls == 0
