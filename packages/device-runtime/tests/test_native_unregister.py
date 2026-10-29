"""Unregister must be the supported way to reopen the package erase guard."""

import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from device_runtime import native_helper as nh


@pytest.fixture
def registered(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    unit_dir = tmp_path / "units"
    root = tmp_path / "instance"
    for directory in (registry, unit_dir, root, root / "config"):
        directory.mkdir()
    (root / ".installation.lock").touch()
    (root / "instance.json").write_text('{"instance_id":"probe"}')
    (root / "active.json").write_text('{"roles":["client"]}')
    (root / "config/roles.json").write_text('{"roles":["client"]}')
    from conftest import write_entry

    # The packaged runner uses an absolute interpreter path, never /usr/bin/env.
    write_entry(root, python=Path(sys.executable).resolve())
    (root / "run").chmod(0o700)
    environment = tmp_path / "environment"
    environment.write_text("fixture")
    environment.chmod(0o600)
    uid = root.stat().st_uid
    if uid == 0:
        pytest.skip("fixture requires non-root file owner")
    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)
    monkeypatch.setattr(nh, "_trusted", lambda *a, **k: None)
    monkeypatch.setattr(nh, "_trusted_marker", lambda *a, **k: None)
    monkeypatch.setattr(nh, "management_lease", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(nh.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=uid, pw_gid=1))
    nh.register(
        registry,
        unit_dir,
        instance_id="probe",
        root=root,
        username="fixture",
        environment_file=environment,
    )
    state = {"active": "inactive"}
    monkeypatch.setattr(nh, "Systemd", lambda: _Adapter(state))
    return registry, unit_dir, root, state


class _Adapter:
    def __init__(self, state):
        self.state = state

    def query(self, unit):
        return {"ActiveState": self.state["active"], "SubState": "dead", "MainPID": "0", "Job": ""}


def test_unregister_removes_enable_links_for_this_unit(registered):
    """A dangling .wants link would survive the erase guard as a ghost unit."""
    registry, unit_dir, _root, _current = registered
    wants = unit_dir / "multi-user.target.wants"
    wants.mkdir()
    link = wants / "ai-device-client-probe.service"
    link.symlink_to(unit_dir / "ai-device-client-probe.service")
    other = wants / "unrelated.service"
    other.symlink_to(unit_dir / "unrelated.service")
    result = nh.unregister(registry, unit_dir, instance_id="probe")
    assert not link.exists() and not link.is_symlink()
    assert other.is_symlink()
    assert result["removed_enable_links"] == [str(link)]


def test_unregister_ignores_links_that_target_another_unit(registered):
    """Deletion follows the link name, never the target it points at."""
    registry, unit_dir, _root, _current = registered
    wants = unit_dir / "multi-user.target.wants"
    wants.mkdir()
    bait = wants / "ai-device-client-probe.service"
    bait.symlink_to("/etc/passwd")
    result = nh.unregister(registry, unit_dir, instance_id="probe")
    assert result["removed_enable_links"] == []
    assert bait.is_symlink()
    assert not (unit_dir / "ai-device-client-probe.service").exists()


@pytest.mark.parametrize("state", ["inactive", "failed"])
def test_unregister_removes_only_registration_artifacts(registered, state):
    registry, unit_dir, root, current = registered
    current["active"] = state
    data = (root / "active.json").read_bytes()
    result = nh.unregister(registry, unit_dir, instance_id="probe")
    assert result["data_preserved"] is True
    assert result["unit"] == "ai-device-client-probe.service"
    assert not (unit_dir / "ai-device-client-probe.service").exists()
    assert not (registry / "probe.json").exists()
    # Instance data and its marker stay untouched: uninstall keeps data.
    assert (root / "active.json").read_bytes() == data
    assert (root / nh.MARKER).exists()


def test_unregister_refuses_while_the_service_is_active(registered):
    registry, unit_dir, _root, current = registered
    current["active"] = "active"
    with pytest.raises(nh.HelperError, match="stop the instance service"):
        nh.unregister(registry, unit_dir, instance_id="probe")
    assert (unit_dir / "ai-device-client-probe.service").exists()
    assert (registry / "probe.json").exists()


def test_unregister_refuses_when_systemd_state_is_unknown(registered, monkeypatch):
    """An unreachable systemd is not evidence that the unit is idle."""
    registry, unit_dir, _, _ = registered

    def boom(_unit):
        raise nh.HelperError("invalid systemd unit observation")

    monkeypatch.setattr(nh, "Systemd", lambda: type("A", (), {"query": staticmethod(boom)})())
    with pytest.raises(nh.HelperError):
        nh.unregister(registry, unit_dir, instance_id="probe")
    assert (registry / "probe.json").exists()


def test_unregister_rejects_an_unknown_instance(registered):
    registry, unit_dir, _, _ = registered
    with pytest.raises(nh.HelperError, match="no registration found"):
        nh.unregister(registry, unit_dir, instance_id="other")


def test_unregister_rejects_a_record_for_another_unit(registered):
    registry, unit_dir, _, _ = registered
    record = registry / "probe.json"
    value = json.loads(record.read_bytes())
    value["unit"] = "ai-device-client-someone-else.service"
    record.write_text(json.dumps(value))
    with pytest.raises(nh.HelperError, match="does not match this instance"):
        nh.unregister(registry, unit_dir, instance_id="probe")


def test_unregister_refuses_a_dangling_registration(registered):
    """A record without its unit needs reconciliation, not silent repair."""
    registry, unit_dir, _, _ = registered
    (unit_dir / "ai-device-client-probe.service").unlink()
    with pytest.raises(nh.HelperError, match="unit is missing"):
        nh.unregister(registry, unit_dir, instance_id="probe")
    assert (registry / "probe.json").exists()


def test_unregister_requires_root(registered, monkeypatch):
    registry, unit_dir, _, _ = registered
    monkeypatch.setattr(nh.os, "geteuid", lambda: 1000)
    with pytest.raises(nh.HelperError, match="administrator privileges"):
        nh.unregister(registry, unit_dir, instance_id="probe")


def test_unregister_accepts_the_native_management_marker(registered, monkeypatch):
    """Unregister must use the registration lease mode, not a plain lease.

    A plain management_lease rejects any registered (marker-bearing) instance,
    so this exercises the real lease instead of the fixture's nullcontext stub.
    """
    registry, unit_dir, root, _ = registered
    real_lease = __import__(
        "device_runtime.management_lock", fromlist=["management_lease"]
    ).management_lease
    monkeypatch.setattr(nh, "management_lease", real_lease)
    assert (root / nh.MARKER).exists()
    result = nh.unregister(registry, unit_dir, instance_id="probe")
    assert result["data_preserved"] is True
    assert not (registry / "probe.json").exists()


def test_deregistration_lease_requires_the_marker(tmp_path):
    """Only a native-registered instance may be deregistered."""
    from device_runtime.management_lock import management_lease

    root = tmp_path / "instance"
    root.mkdir()
    with pytest.raises(ValueError, match="registered native instance"), management_lease(
        root, deregistration=True
    ):
        pass
    (root / nh.MARKER).write_text("{}")
    with management_lease(root, deregistration=True):
        pass


def test_deregistration_lease_rejects_a_symlinked_marker(tmp_path):
    from device_runtime.management_lock import management_lease

    root = tmp_path / "instance"
    root.mkdir()
    (root / nh.MARKER).symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="registered native instance"), management_lease(
        root, deregistration=True
    ):
        pass


def test_unregister_keeps_the_instance_registerable_again(registered, monkeypatch):
    """Unregister drops the registration, not the instance identity.

    The marker stays, so re-registering the same instance must go through the
    explicit recovery path rather than silently overwriting the baseline.
    """
    registry, unit_dir, _root, _ = registered
    real_lease = __import__(
        "device_runtime.management_lock", fromlist=["management_lease"]
    ).management_lease
    monkeypatch.setattr(nh, "management_lease", real_lease)
    nh.unregister(registry, unit_dir, instance_id="probe")
    kwargs = {
        "instance_id": "probe",
        "root": _root,
        "username": "fixture",
        "environment_file": next(_root.parent.glob("environment")),
    }
    # A fresh register must refuse because the marker still marks this instance
    # as native-managed; re-registering is the explicit recovery path.
    with pytest.raises(ValueError, match="native lifecycle manager"):
        nh.register(registry, unit_dir, **kwargs)
