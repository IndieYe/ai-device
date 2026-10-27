import json
from pathlib import Path

import pytest

from device_runtime import native_prepare as np


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    root = tmp_path / "instance"
    root.mkdir()
    (root / ".installation.lock").touch()
    (root / "active.json").write_text('{"roles":["client"],"release_version":"1"}')
    (root / np.MARKER).write_text("fixture binding")
    uv = tmp_path / "uv"
    uv.write_text("#!/bin/sh\nexit 99\n")
    uv.chmod(0o700)
    envelope = tmp_path / "release.json"
    envelope.write_text('{"payload":{"release_id":"new"}}')
    binding = {"uid": 12345, "root": str(root)}
    active = json.loads((root / "active.json").read_text())
    monkeypatch.setattr(np, "_binding", lambda _: (binding.copy(), active.copy()))
    monkeypatch.setattr(np.helper, "_trusted", lambda *a, **kw: None)
    monkeypatch.setattr(np.candidate, "verify_release", lambda *a, **kw: {"release_id": "new"})
    calls = []

    def extract(package, envelope, destination, **kwargs):
        bundle = destination / "root/opt/payload/bundle"
        bundle.mkdir(parents=True)
        (bundle / "manifest.json").write_text('{"fixture":true}')
        return {
            "payload_root": "opt/payload",
            "release_sha256": np.hashlib.sha256(
                np.canonical({"release_id": "new"}).encode()
            ).hexdigest(),
        }

    manifest = {"fixture": True}
    monkeypatch.setattr(np.candidate, "prepare_candidate", extract)
    monkeypatch.setattr(np.core, "inspect_bundle", lambda _: manifest)

    def build(root, bundle, manifest, roles, **kwargs):
        calls.append((root, bundle, roles, kwargs))
        home = root / "releases/new-release"
        (home / "bundle").mkdir(parents=True)
        (home / "bundle/manifest.json").write_text(json.dumps(manifest))
        python = home / "client/venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("fixture interpreter")
        python.chmod(0o700)
        receipt = {"release": "new-release", "bundle_sha256": np.core._fingerprint(manifest)}
        (home / "installation.json").write_text(json.dumps(receipt))
        return receipt

    monkeypatch.setattr(np.core, "_prepare", build)
    options = {
        "trusted_keys": {},
        "release_id": "new",
        "operation_id": "one",
        "bootstrap_version": "1",
        "uv_executable": uv,
    }
    return root, envelope, options, calls


def prepare(preparation, **changes):
    root, envelope, options, _ = preparation
    return np.prepare_runtime_candidate(root, "fixture.deb", envelope, **{**options, **changes})


def test_native_prepare_no_active_mutation_and_no_replay(preparation):
    root, _, options, calls = preparation
    before = (root / "active.json").read_bytes()
    result = prepare(preparation)
    assert result["state"] == "runtime_prepared" and result["activated"] is False
    assert calls[0][2] == ["client"]
    assert calls[0][3]["uv_executable"] == options["uv_executable"]
    assert prepare(preparation) == result
    assert len(calls) == 1
    assert (root / "active.json").read_bytes() == before


def test_failure_has_receipt_and_same_id_not_replayed(preparation, monkeypatch):
    calls = []

    def fail(*a, **kw):
        calls.append(True)
        raise ValueError("injected")

    monkeypatch.setattr(np.core, "_prepare", fail)
    with pytest.raises(ValueError, match="injected"):
        prepare(preparation)
    with pytest.raises(np.PreparationError, match="failed"):
        prepare(preparation)
    with pytest.raises(np.PreparationError, match="failed"):
        prepare(preparation, operation_id="two")
    assert len(calls) == 1
    result = json.loads((preparation[0] / "native-candidates/one.result.json").read_text())
    assert result["state"] == "failed"


def test_interrupted_intent_blocks_same_and_new_id(preparation, monkeypatch):
    def interrupt(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(np.core, "_prepare", interrupt)
    with pytest.raises(KeyboardInterrupt):
        prepare(preparation)
    with pytest.raises(np.PreparationError, match="incomplete"):
        prepare(preparation)
    with pytest.raises(np.PreparationError, match="incomplete"):
        prepare(preparation, operation_id="two")


def test_source_changed_during_prepare_not_completed(preparation, monkeypatch):
    real = np.core._prepare

    def drift(*a, **kw):
        result = real(*a, **kw)
        (preparation[0] / "active.json").write_text('{"changed":true}')
        return result

    monkeypatch.setattr(np.core, "_prepare", drift)
    with pytest.raises(np.PreparationError, match="source changed"):
        prepare(preparation)
    result = json.loads((preparation[0] / "native-candidates/one.result.json").read_text())
    assert result["state"] == "failed"


def test_changed_completed_evidence_refused(preparation):
    prepare(preparation)
    (preparation[0] / "releases/new-release/installation.json").write_text("{}")
    with pytest.raises(np.PreparationError, match="evidence changed"):
        prepare(preparation)
    assert len(preparation[3]) == 1


def test_source_changed_between_requests_conflicts(preparation):
    prepare(preparation)
    (preparation[0] / "active.json").write_text('{"new-source":true}')
    with pytest.raises(np.PreparationError, match="conflict"):
        prepare(preparation)


def test_maintenance_exclusive_lock_blocks_preparation(preparation):
    root = preparation[0]
    with (root / ".installation.lock").open("rb") as lease:
        np.fcntl.flock(lease, np.fcntl.LOCK_EX)
        with pytest.raises(np.PreparationError, match="maintenance busy"):
            prepare(preparation)
    assert not (root / "native-candidates").exists()


def test_running_shared_lock_allows_preparation(preparation):
    with (preparation[0] / ".installation.lock").open("rb") as lease:
        np.fcntl.flock(lease, np.fcntl.LOCK_SH)
        assert prepare(preparation)["state"] == "runtime_prepared"


def test_invalid_tool_rejected_before_intent(preparation):
    with pytest.raises(np.PreparationError, match="persistent uv"):
        prepare(preparation, uv_executable=Path("relative"))
    assert not (preparation[0] / "native-candidates").exists()


def test_binding_requires_registered_nonroot_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(np.sys, "platform", "linux")
    monkeypatch.setattr(np.os, "geteuid", lambda: 0)
    with pytest.raises(np.PreparationError, match="not root"):
        np._binding(tmp_path)


def test_completed_runtime_missing_interpreter_refused(preparation):
    prepare(preparation)
    (preparation[0] / "releases/new-release/client/venv/bin/python").unlink()
    with pytest.raises(np.PreparationError, match="interpreter unavailable"):
        prepare(preparation)
    assert len(preparation[3]) == 1


def test_owner_binding_detects_marker_drift(tmp_path, monkeypatch):
    # Exercise production binding validation, not the preparation fixture stub.
    root = tmp_path / "instance"
    root.mkdir()
    for name, value in [
        ("run", "runner"),
        ("instance.json", '{"instance_id":"probe"}'),
        ("config/roles.json", "{}"),
        ("active.json", '{"roles":["client"]}'),
    ]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    unit = tmp_path / "client.service"
    unit.write_text("unit")
    binding = {
        "uid": root.stat().st_uid,
        "root": str(root),
        "instance_id": "probe",
        "unit_path": str(unit),
        "unit_sha256": np.helper._digest(unit),
    }
    for name, key in [
        ("run", "runner_sha256"),
        ("instance.json", "identity_sha256"),
        ("config/roles.json", "roles_sha256"),
    ]:
        binding[key] = np.helper._digest(root / name)
    (root / np.MARKER).write_text(json.dumps({"schema_version": 2, "binding": binding}))
    monkeypatch.setattr(np.sys, "platform", "linux")
    monkeypatch.setattr(np.helper, "_trusted_marker", lambda _: None)
    monkeypatch.setattr(np.helper, "_trusted", lambda _: None)
    assert np._binding(root)[0] == binding
    (root / "run").write_text("changed")
    with pytest.raises(np.PreparationError, match="binding changed"):
        np._binding(root)
