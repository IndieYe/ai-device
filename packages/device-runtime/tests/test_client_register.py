import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from device_runtime import client_management
from device_runtime import client_register as module


@pytest.fixture
def registration(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "check_conflicts", lambda: None)
    locks = (tmp_path / "dpkg-frontend", tmp_path / "dpkg")
    for lock in locks:
        lock.touch()
    monkeypatch.setattr(client_management, "DPKG_LOCKS", locks)
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    for name in ("client.json", "token"):
        (config / name).write_text("fixture")
        (config / name).chmod(0o600)
    monkeypatch.setattr(module, "CONFIG", config)
    monkeypatch.setattr(module, "STATE", tmp_path / "state")
    monkeypatch.setattr(module, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(module, "UNIT_PATH", tmp_path / "client.service")
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        module.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
    )
    monkeypatch.setattr(module.os, "chown", lambda *args: None)
    original_open = Path.open

    def open_path(path, *args, **kwargs):
        if str(path) == "/run/ai-device-client-management.lock":
            path = tmp_path / "management.lock"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_path)
    calls = []
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0),
    )
    return calls


def test_register_prepares_private_dirs_and_does_not_start(registration):
    module.register("device", allow_root=True, confirm_legacy_clients_disabled=True)
    assert module.UNIT_PATH.read_text() == module.render_unit("device")
    assert module.STATE.is_dir() and module.LOGS.is_dir()
    assert module.LOGS.stat().st_mode & 0o077 == 0
    assert [call[:2] for call in registration] == [
        ["runuser", "-u"],
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable"],
    ]


def test_existing_unit_not_overwritten(registration):
    module.UNIT_PATH.write_text("unrelated")
    with pytest.raises(ValueError, match="already exists"):
        module.register("device", allow_root=True, confirm_legacy_clients_disabled=True)
    assert module.UNIT_PATH.read_text() == "unrelated"
    assert registration == []


def test_refuses_public_credentials(registration):
    (module.CONFIG / "token").chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        module.register("device", allow_root=True, confirm_legacy_clients_disabled=True)
    assert registration == []
    assert not module.UNIT_PATH.exists()


def test_probe_failure_does_not_write_unit(registration, monkeypatch):
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match="not usable"):
        module.register("device", allow_root=True, confirm_legacy_clients_disabled=True)
    assert not module.UNIT_PATH.exists()


def test_registration_requires_legacy_confirmation_before_writes(registration):
    from device_runtime.client_conflicts import ClientConflict

    with pytest.raises(ClientConflict, match="confirm"):
        module.register("device", allow_root=True)
    assert not module.STATE.exists() and not module.UNIT_PATH.exists()
    assert registration == []


def test_confirmation_never_bypasses_detected_conflict(registration, monkeypatch):
    from device_runtime.client_conflicts import ClientConflict

    def conflict():
        raise ClientConflict("legacy client instance detected")

    monkeypatch.setattr(module, "check_conflicts", conflict)
    with pytest.raises(ClientConflict, match="legacy"):
        module.register("device", allow_root=True, confirm_legacy_clients_disabled=True)
    assert not module.STATE.exists() and not module.UNIT_PATH.exists()
    assert registration == []
