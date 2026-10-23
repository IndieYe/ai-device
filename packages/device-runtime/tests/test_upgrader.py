"""Single-instance upgrade tests; pytest directories only, no real services."""

import fcntl

import pytest
from test_instance_lifecycle import (
    active,
    all_bytes,
)
from test_instance_lifecycle import delivery as delivery  # noqa: PLC0414 -- pytest registration
from test_instance_lifecycle import instance as instance  # noqa: PLC0414 -- pytest registration
from test_instance_lifecycle import simulated_installer as simulated_installer  # noqa: PLC0414 -- pytest registration
from test_instance_lifecycle import upgraded as upgraded  # noqa: PLC0414 -- pytest registration

from device_runtime import installation as install
from device_runtime import upgrader as up


def test_online_prepare_with_real_shared_runtime_lease(instance, upgraded):
    before = all_bytes(instance / "state")
    original = active(instance)
    with (instance / ".installation.lock").open("rb") as lease:
        fcntl.flock(lease, fcntl.LOCK_SH)
        record = up.prepare(instance, upgraded, "upgrade-one")
        assert record["state"] == "prepared"
        assert active(instance) == original
        with pytest.raises(install.InstallationError):
            up.activate(instance, "upgrade-one", stopped=True)
    assert all_bytes(instance / "state") == before
    result = up.activate(instance, "upgrade-one", stopped=True)
    assert result["state"] == "published"
    assert active(instance)["release"] == record["candidate"]["release"]
    assert up.activate(instance, "upgrade-one", stopped=True) == result
    assert all_bytes(instance / "state") == before


def test_prepare_idempotence_and_conflict(instance, upgraded, delivery, simulated_installer):
    first = up.prepare(instance, upgraded, "same")
    calls = len(simulated_installer)
    assert up.prepare(instance, upgraded, "same") == first
    assert len(simulated_installer) == calls
    with pytest.raises(up.UpgradeError, match="conflict"):
        up.prepare(instance, delivery, "same")
    with pytest.raises(up.UpgradeError, match="nonterminal"):
        up.prepare(instance, delivery, "other")


def test_stale_configuration_refuses_switch(instance, upgraded):
    up.prepare(instance, upgraded, "one")
    config = instance / "config/roles.json"
    data = up.life._read(config)
    data["center"]["port"] += 1
    install._json(config, data)
    with pytest.raises(up.UpgradeError, match="stale"):
        up.activate(instance, "one", stopped=True)


def test_prepare_failure_is_not_replayed(instance, upgraded, monkeypatch):
    original = active(instance)
    monkeypatch.setattr(
        install, "_prepare", lambda *a, **k: (_ for _ in ()).throw(ValueError("sensitive"))
    )
    with pytest.raises(up.UpgradeError, match="preparation failed"):
        up.prepare(instance, upgraded, "one")
    assert up.status(instance, "one")["state"] == "failed"
    assert up.prepare(instance, upgraded, "one")["state"] == "failed"
    with pytest.raises(up.UpgradeError, match="reconciliation"):
        up.activate(instance, "one", stopped=True)
    assert active(instance) == original


@pytest.mark.parametrize("published", [True, False])
def test_interrupted_pointer_reconciliation(instance, upgraded, monkeypatch, published):
    up.prepare(instance, upgraded, "one")
    save = up._save

    def fail(path, value):
        if path.name == "active.json" and not published:
            raise OSError("interrupt before pointer")
        if path.name == "one.json" and value["state"] == "published":
            raise OSError("interrupt after pointer")
        save(path, value)

    monkeypatch.setattr(up, "_save", fail)
    with pytest.raises(OSError):
        up.activate(instance, "one", stopped=True)
    monkeypatch.setattr(up, "_save", save)
    result = up.reconcile(instance, "one")
    assert result["state"] == ("published" if published else "unknown")


def test_readonly_status_and_confirmation(instance, upgraded):
    before = all_bytes(instance)
    with pytest.raises(FileNotFoundError):
        up.status(instance, "missing")
    assert all_bytes(instance) == before
    up.prepare(instance, upgraded, "one")
    with pytest.raises(up.UpgradeError, match="stopped"):
        up.activate(instance, "one")


def test_online_prepare_rejects_exclusive_maintenance(instance, upgraded):
    with install._lock(instance), pytest.raises(up.UpgradeError, match="maintenance"):
        up.prepare(instance, upgraded, "blocked")
    assert not (instance / "upgrades/blocked.json").exists()


def test_cli_status_is_bounded_and_redacted(instance, upgraded, monkeypatch, capsys):
    from device_runtime import cli

    up.prepare(instance, upgraded, "one")
    monkeypatch.setattr(
        "sys.argv",
        ["device-runtime", "upgrade", "status", "--root", str(instance), "--upgrade-id", "one"],
    )
    cli.main()
    import json

    value = json.loads(capsys.readouterr().out)
    assert set(value) == {"upgrade_id", "state", "release"}
    assert value["state"] == "prepared"


def test_legacy_runner_is_not_silently_adopted(instance, upgraded):
    (instance / "run").write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(install.InstallationError, match="incompatible"):
        up.prepare(instance, upgraded, "one")
    assert not (instance / "upgrades").exists()


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644])
def test_publish_preserves_boot_pointer_read_contract(instance, upgraded, mode):
    import stat

    pointer = instance / "active.json"
    pointer.chmod(mode)
    before = pointer.stat()
    up.prepare(instance, upgraded, "one")
    up.activate(instance, "one", stopped=True)
    after = pointer.stat()
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert stat.S_IMODE(after.st_mode) == mode
    assert (instance / "upgrades/one.json").stat().st_mode & 0o777 == 0o600


def test_publish_rejects_writable_boot_pointer(instance, upgraded):
    up.prepare(instance, upgraded, "one")
    pointer = instance / "active.json"
    pointer.chmod(0o666)
    before = pointer.read_bytes()
    with pytest.raises(up.UpgradeError, match="unsafe permissions"):
        up.activate(instance, "one", stopped=True)
    assert pointer.read_bytes() == before
