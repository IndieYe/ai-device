"""Cooperative ownership and legacy/native mutual exclusion."""

import pytest

from device_runtime import installation, upgrade_supervisor, upgrader
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


def test_native_preparation_lease_keeps_legacy_fence(tmp_path):
    from device_runtime.management_lock import native_preparation_lease

    with pytest.raises(ValueError, match="registered instance"), native_preparation_lease(tmp_path):
        pass
    (tmp_path / MARKER).write_text("caller validates binding separately")
    with native_preparation_lease(tmp_path):
        with pytest.raises(ValueError, match="active"), native_preparation_lease(tmp_path):
            pass
        with pytest.raises(ValueError, match="active"), management_lease(tmp_path):
            pass
    with pytest.raises(ValueError, match="native-managed"), management_lease(tmp_path):
        pass


@pytest.mark.parametrize(
    "operation",
    [
        (upgrader, "prepare"),
        (upgrader, "activate"),
        (upgrader, "reconcile"),
        (upgrade_supervisor, "supervise"),
        (upgrade_supervisor, "recover"),
        (upgrade_supervisor, "resume_admission"),
        (upgrade_supervisor, "reconcile_supervision"),
    ],
)
def test_legacy_upgrade_entries_refuse_managed_instance_before_body(tmp_path, operation):
    """Each legacy entry is decorated and must refuse a managed instance.

    The decorated wrapper takes the lease before calling the body, so a managed
    marker rejects the call prior to any systemctl or record access. No real
    unit or service is touched here.
    """
    module, name = operation
    root = tmp_path / "instance"
    root.mkdir()
    (root / ".native-management.json").write_text("{}")
    with pytest.raises(ValueError, match="native-managed instance requires"):
        getattr(module, name)(root)
