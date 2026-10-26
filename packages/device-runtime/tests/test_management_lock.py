"""Cooperative ownership and legacy/native mutual exclusion."""

import json
from pathlib import Path

import pytest

from device_runtime import installation, native_helper, upgrade_supervisor, upgrader
from device_runtime.management_lock import MARKER, management_lease


def test_registration_excludes_legacy_and_other_registration(tmp_path):
    with management_lease(tmp_path, registration=True):
        with pytest.raises(ValueError, match="active"), management_lease(tmp_path):
            pass
        with (
            pytest.raises(ValueError, match="active"),
            management_lease(tmp_path, registration=True),
        ):
            pass
    with management_lease(tmp_path), management_lease(tmp_path):  # noqa: SIM117
        with (
            pytest.raises(ValueError, match="active"),
            management_lease(tmp_path, registration=True),
        ):
            pass


def test_marker_blocks_installation_lock_without_touching_installation(tmp_path):
    (tmp_path / MARKER).write_text("pending registration")
    with pytest.raises(ValueError, match="native-managed"), installation._lock(tmp_path):
        pass
    assert not (tmp_path / ".installation.lock").exists()


@pytest.mark.parametrize(
    "operation,args,kwargs",
    [
        (upgrader.prepare, ("missing-bundle", "one"), {}),
        (upgrader.activate, ("one",), {"stopped": True}),
        (upgrader.reconcile, ("one",), {}),
        (upgrade_supervisor.supervise, ("one",), {"confirmed": True}),
        (
            upgrade_supervisor.recover,
            ("one",),
            {"action": "start", "expected_record_sha256": "x", "confirmed": True},
        ),
        (upgrade_supervisor.resume_admission, ("one",), {"confirmed": True}),
        (upgrade_supervisor.reconcile_supervision, ("one",), {}),
    ],
)
def test_managed_legacy_entrypoints_refuse_before_side_effects(tmp_path, operation, args, kwargs):
    (tmp_path / MARKER).write_text("pending or registered")
    with pytest.raises(ValueError, match="native-managed"):
        operation(tmp_path, *args, **kwargs)
    assert not (tmp_path / "upgrades").exists()


def test_management_symlink_alias_uses_same_lock(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with management_lease(root, registration=True):  # noqa: SIM117
        with pytest.raises(ValueError, match="active"), management_lease(alias):
            pass


def test_symlink_lock_refused(tmp_path):
    target = tmp_path / "target"
    target.touch()
    (tmp_path / ".management.lock").symlink_to(target)
    with pytest.raises(OSError), management_lease(tmp_path):
        pass


@pytest.mark.parametrize(
    "scenario",
    ["record-missing", "marker-only", "unit-conflict", "drift", "legacy", "record-conflict"],
)
def test_registration_fence_published_before_unit_and_record(tmp_path, monkeypatch, scenario):
    root = tmp_path / "instance"
    (root / "config").mkdir(parents=True)
    (root / ".installation.lock").touch()
    (root / "instance.json").write_text('{"instance_id":"probe"}')
    (root / "active.json").write_text('{"roles":["client"]}')
    (root / "config/roles.json").write_text('{"client":{}}')
    python = tmp_path / "python"
    python.touch()
    runner = root / "run"
    runner.write_text(f"#!{python}\n")
    runner.chmod(0o755)
    registry, units = tmp_path / "registry", tmp_path / "units"
    registry.mkdir()
    units.mkdir()
    environment = tmp_path / "credential"
    environment.touch(mode=0o600)
    import os
    from types import SimpleNamespace

    account = SimpleNamespace(pw_uid=os.getuid() or 12345, pw_gid=os.getgid())
    monkeypatch.setattr(native_helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(native_helper.pwd, "getpwnam", lambda name: account)
    monkeypatch.setattr(native_helper, "_trusted", lambda *a, **kw: None)
    # On a root runner the fixture instance owner must still be non-root.
    if os.getuid() == 0:
        os.chown(root, account.pw_uid, account.pw_gid)
    monkeypatch.setattr(native_helper, "_trusted_marker", lambda path: None)
    real_save = native_helper._save_new

    def save(path, value):
        if path.parent == registry:
            assert (root / MARKER).exists()
            with pytest.raises(ValueError, match="active"), management_lease(root):
                pass
            raise OSError("injected record failure")
        real_save(path, value)

    monkeypatch.setattr(native_helper, "_save_new", save)
    with pytest.raises(OSError, match="record failure"):
        native_helper.register(
            registry,
            units,
            instance_id="probe",
            root=root,
            username="fixture",
            environment_file=environment,
        )
    assert json.loads((root / MARKER).read_text())["instance_id"] == "probe"
    with pytest.raises(ValueError, match="native-managed"), management_lease(root):
        pass
    assert not (registry / "probe.json").exists()
    monkeypatch.setattr(native_helper, "_save_new", real_save)
    unit = units / "ai-device-client-probe.service"
    if scenario == "marker-only":
        unit.unlink()
    if scenario == "unit-conflict":
        unit.write_text("conflicting unit")
    if scenario == "drift":
        (root / "config/roles.json").write_text('{"changed":true}')
    if scenario == "legacy":
        (root / MARKER).write_text('{"schema_version":1}')
    if scenario == "record-conflict":
        (registry / "probe.json").write_text('{"conflict":true}')
    kwargs = {
        "instance_id": "probe",
        "root": root,
        "username": "fixture",
        "environment_file": environment,
        "recover": True,
    }
    if scenario in {"unit-conflict", "drift", "legacy", "record-conflict"}:
        before = {str(p): p.read_bytes() for p in [root / MARKER, unit] if p.exists()}
        with pytest.raises(native_helper.HelperError):
            native_helper.register(registry, units, **kwargs)
        assert all(Path(p).read_bytes() == value for p, value in before.items())
    else:
        result = native_helper.register(registry, units, **kwargs)
        assert result["started"] is False and result["enabled"] is False
        before = (registry / "probe.json").read_bytes()
        native_helper.register(registry, units, **kwargs)
        assert (registry / "probe.json").read_bytes() == before
        assert unit.exists()


def test_registration_lock_blocks_other_process(tmp_path):
    import subprocess
    import sys

    code = """import sys
from device_runtime.management_lock import management_lease
try:
    with management_lease(sys.argv[1]): pass
except ValueError: sys.exit(17)
"""
    with management_lease(tmp_path, registration=True):
        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)], check=False, capture_output=True
        )
        assert result.returncode == 17, result.stderr
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], check=False, capture_output=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "uid,mode,accepted",
    [
        (0, 0o100600, True),
        (0, 0o100644, True),
        (501, 0o100600, False),
        (0, 0o100660, False),
        (0, 0o120777, False),
    ],
)
def test_marker_trust_checks_file_not_user_owned_ancestors(uid, mode, accepted):
    from types import SimpleNamespace

    class Marker:
        def lstat(self):
            return SimpleNamespace(st_uid=uid, st_mode=mode)

    if accepted:
        native_helper._trusted_marker(Marker())
    else:
        with pytest.raises(native_helper.HelperError, match="management marker"):
            native_helper._trusted_marker(Marker())
