"""Registration recovery must not conflate JSON booleans with numeric fields."""

import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from device_runtime import native_helper as nh


@pytest.fixture
def registration(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    unit_dir = tmp_path / "units"
    root = tmp_path / "instance"
    for directory in (registry, unit_dir, root, root / "config"):
        directory.mkdir()
    (root / ".installation.lock").touch()
    (root / "instance.json").write_text('{"instance_id":"probe"}')
    (root / "active.json").write_text('{"roles":["client"]}')
    (root / "config/roles.json").write_text('{"roles":["client"]}')
    runner = root / "run"
    runner.write_text(f"#!{Path(sys.executable).resolve()}\n")
    runner.chmod(0o700)
    environment = tmp_path / "environment"
    environment.write_text("fixture")
    environment.chmod(0o600)
    uid = root.stat().st_uid
    if uid == 0:
        pytest.skip("fixture requires non-root file owner")
    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)
    monkeypatch.setattr(nh, "_trusted", lambda *a, **k: None)
    monkeypatch.setattr(nh, "_trusted_marker", lambda *a, **k: None)
    monkeypatch.setattr(nh, "management_lease", lambda *a, **k: nullcontext())
    monkeypatch.setattr(nh.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=uid, pw_gid=1))
    kwargs = {"instance_id": "probe", "root": root, "username": "fixture", "environment_file": environment}
    nh.register(registry, unit_dir, **kwargs)
    return registry, unit_dir, root, kwargs


@pytest.mark.parametrize("kind", ["marker-schema", "marker-gid", "record-schema", "record-gid"])
def test_recover_registration_rejects_boolean_number_drift(registration, kind):
    registry, unit_dir, root, kwargs = registration
    marker = root / nh.MARKER
    record = registry / "probe.json"
    unit = unit_dir / "ai-device-client-probe.service"
    if kind.startswith("marker"):
        value = json.loads(marker.read_bytes())
        if kind == "marker-schema":
            value["binding"]["schema_version"] = True
        else:
            value["binding"]["gid"] = True
        marker.write_text(json.dumps(value))
        # Align the record's digest so this does not depend on the hash check.
        saved = json.loads(record.read_bytes())
        saved["management_sha256"] = nh._digest(marker)
        record.write_text(json.dumps(saved))
    else:
        value = json.loads(record.read_bytes())
        value["schema_version" if kind == "record-schema" else "gid"] = True
        record.write_text(json.dumps(value))
    before = {path: path.read_bytes() for path in (marker, record, unit)}
    with pytest.raises(nh.HelperError, match="baseline differs|artifact conflict"):
        nh.register(registry, unit_dir, **kwargs, recover=True)
    assert {path: path.read_bytes() for path in before} == before


def test_recover_registration_reuses_identical_artifacts(registration):
    registry, unit_dir, root, kwargs = registration
    paths = (root / nh.MARKER, registry / "probe.json", unit_dir / "ai-device-client-probe.service")
    before = {path: path.read_bytes() for path in paths}
    result = nh.register(registry, unit_dir, **kwargs, recover=True)
    assert result["started"] is False and result["enabled"] is False
    assert {path: path.read_bytes() for path in paths} == before


def test_recovery_conflict_does_not_recreate_missing_unit(registration):
    registry, unit_dir, root, kwargs = registration
    record = registry / "probe.json"
    marker = root / nh.MARKER
    unit = unit_dir / "ai-device-client-probe.service"
    value = json.loads(record.read_bytes())
    value["gid"] = True
    record.write_text(json.dumps(value))
    unit.unlink()
    before = {path: path.read_bytes() for path in (record, marker)}
    with pytest.raises(nh.HelperError, match="artifact conflict"):
        nh.register(registry, unit_dir, **kwargs, recover=True)
    assert not unit.exists()
    assert {path: path.read_bytes() for path in before} == before
