import os
from types import SimpleNamespace

import pytest

from device_runtime import client_macos_register as reg
from device_runtime.client_macos_identity import MacIdentityError, read_identity, reserve_identity
from device_runtime.client_macos_layout import MacClientLayout
from device_runtime.client_macos_management import management_lock
from device_runtime.client_macos_plist import PlistPublishError


@pytest.fixture
def environment(tmp_path, monkeypatch):
    uid, gid = os.getuid(), os.getgid()
    layout = MacClientLayout(uid, tmp_path / "home")
    layout.directory.mkdir(parents=True, mode=0o700)
    directory = tmp_path / "management"
    directory.mkdir(mode=0o700)
    account = SimpleNamespace(name="test", gid=gid, layout=layout)
    reserve_identity(account, directory=directory)
    # Capture actual reader before the administrator gate is simulated.
    identity = read_identity(directory=directory)
    monkeypatch.setattr(reg.sys, "platform", "darwin")
    monkeypatch.setattr(reg.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        reg, "management_lock", lambda: management_lock(directory, owner_uid=uid, owner_gid=gid)
    )
    monkeypatch.setattr(reg, "resolve_account", lambda _: account)
    monkeypatch.setattr(reg, "read_identity", lambda: identity.copy())
    monkeypatch.setattr(reg, "reserve_identity", lambda _: pytest.fail("must not reserve again"))
    monkeypatch.setattr(reg, "check_registration_conflicts", lambda **_: None)
    monkeypatch.setattr(reg, "validate_launcher", lambda: None)
    monkeypatch.setattr(reg, "validate_registration_files", lambda _: None)
    monkeypatch.setattr(reg, "validate_config_as_user", lambda _: None)
    return account, directory, identity


def test_resume_preserves_identity_and_publishes_once(environment):
    account, directory, _ = environment
    record = directory / "registration.json"
    before = record.read_bytes(), record.stat().st_ino
    result = reg.resume_registration("test", confirm_legacy_clients_disabled=True)
    assert result["registered"] and not result["started"]
    assert account.layout.plist.exists()
    assert before == (record.read_bytes(), record.stat().st_ino)
    with pytest.raises(PlistPublishError):
        reg.resume_registration("test", confirm_legacy_clients_disabled=True)


@pytest.mark.parametrize(
    "field,value", [("user", "other"), ("uid", 99999), ("gid", 99999), ("home", "/other")]
)
def test_changed_identity_rejected_before_user_writes(environment, field, value):
    account, directory, identity = environment
    identity[field] = value
    with pytest.raises(MacIdentityError, match="rebinding"):
        reg.resume_registration("test", confirm_legacy_clients_disabled=True)
    assert not account.layout.state.exists()
    assert not account.layout.plist.exists()
    assert (directory / "registration.json").exists()


def test_resume_rechecks_config_before_creating_directories(environment, monkeypatch):
    account, _, _ = environment

    def fail(_):
        raise ValueError("config unavailable")

    monkeypatch.setattr(reg, "validate_config_as_user", fail)
    with pytest.raises(ValueError, match="config"):
        reg.resume_registration("test", confirm_legacy_clients_disabled=True)
    assert not account.layout.state.exists()


def test_failed_registration_then_explicit_resume(tmp_path, monkeypatch):
    uid, gid = os.getuid(), os.getgid()
    layout = MacClientLayout(uid, tmp_path / "home")
    layout.directory.mkdir(parents=True, mode=0o700)
    directory = tmp_path / "management"
    directory.mkdir(mode=0o700)
    account = SimpleNamespace(name="test", gid=gid, layout=layout)
    # Registration's administrator check is simulated; real identity writes
    # retain the test user's ownership in this isolated directory.
    actual_geteuid = os.geteuid

    def as_test_user(function):
        with monkeypatch.context() as context:
            context.setattr(os, "geteuid", actual_geteuid)
            return function()

    monkeypatch.setattr(reg.sys, "platform", "darwin")
    monkeypatch.setattr(reg.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        reg, "management_lock", lambda: management_lock(directory, owner_uid=uid, owner_gid=gid)
    )
    monkeypatch.setattr(reg, "resolve_account", lambda _: account)
    monkeypatch.setattr(
        reg,
        "reserve_identity",
        lambda target: as_test_user(lambda: reserve_identity(target, directory=directory)),
    )
    monkeypatch.setattr(
        reg, "read_identity", lambda: as_test_user(lambda: read_identity(directory=directory))
    )
    monkeypatch.setattr(reg, "check_registration_conflicts", lambda **_: None)
    monkeypatch.setattr(reg, "validate_launcher", lambda: None)
    monkeypatch.setattr(reg, "validate_registration_files", lambda _: None)
    monkeypatch.setattr(reg, "validate_config_as_user", lambda _: None)
    original = reg.prepare_user_directories

    def fail(_):
        raise OSError("injected directory creation failure")

    monkeypatch.setattr(reg, "prepare_user_directories", fail)
    with pytest.raises(OSError, match="injected"):
        reg.register("test", confirm_legacy_clients_disabled=True)
    record = directory / "registration.json"
    saved = record.read_bytes(), record.stat().st_ino
    assert not layout.plist.exists()
    monkeypatch.setattr(reg, "prepare_user_directories", original)
    result = reg.resume_registration("test", confirm_legacy_clients_disabled=True)
    assert result["registered"] and not result["started"]
    assert saved == (record.read_bytes(), record.stat().st_ino)
    assert layout.plist.exists()


def test_resume_start_gate_failure_keeps_identity_and_runtime(environment, monkeypatch):
    account, directory, _ = environment
    record = directory / "registration.json"
    saved = record.read_bytes(), record.stat().st_ino
    original = reg.provision_start_gate

    def fail(*args, **kwargs):
        raise OSError("injected start gate failure")

    monkeypatch.setattr(reg, "provision_start_gate", fail)
    with pytest.raises(OSError, match="start gate"):
        reg.resume_registration("test", confirm_legacy_clients_disabled=True)
    runtime = account.layout.directory / "runtime.lock"
    inode = runtime.stat().st_ino
    assert not account.layout.plist.exists()
    assert saved == (record.read_bytes(), record.stat().st_ino)
    monkeypatch.setattr(reg, "provision_start_gate", original)
    reg.resume_registration("test", confirm_legacy_clients_disabled=True)
    assert runtime.stat().st_ino == inode
    assert (account.layout.directory / "start.lock").is_file()
    assert account.layout.plist.exists()
    assert saved == (record.read_bytes(), record.stat().st_ino)


def test_resume_preserves_existing_start_gate_inode(environment):
    from device_runtime.client_macos_start_gate import prevent_start, provision_start_gate

    account, _, _ = environment
    provision_start_gate(account.layout.directory, uid=account.layout.uid, gid=account.gid)
    gate = account.layout.directory / "start.lock"
    inode = gate.stat().st_ino
    with prevent_start(account.layout.directory, uid=account.layout.uid):
        reg.resume_registration("test", confirm_legacy_clients_disabled=True)
        assert gate.stat().st_ino == inode
    assert gate.stat().st_ino == inode


@pytest.mark.parametrize("kind", ["symlink", "public", "fifo"])
def test_resume_invalid_gate_never_repairs_or_publishes(environment, kind):
    from device_runtime.client_macos_lock import MacLockError

    account, directory, _ = environment
    gate = account.layout.directory / "start.lock"
    if kind == "symlink":
        gate.symlink_to(account.layout.directory / "runtime.lock")
    elif kind == "fifo":
        os.mkfifo(gate, 0o600)
    else:
        gate.touch(mode=0o644)
    before = gate.lstat()
    record = directory / "registration.json"
    saved = record.read_bytes(), record.stat().st_ino
    with pytest.raises(MacLockError):
        reg.resume_registration("test", confirm_legacy_clients_disabled=True)
    after = gate.lstat()
    assert (before.st_ino, before.st_mode) == (after.st_ino, after.st_mode)
    assert saved == (record.read_bytes(), record.stat().st_ino)
    assert not account.layout.plist.exists()
