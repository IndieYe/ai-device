"""Registration must accept an explicit user and never guess one."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from device_runtime import client_register_cli as registration


def _user(name="device", uid=1234, gid=1234, home="/home/device"):
    return SimpleNamespace(pw_name=name, pw_uid=uid, pw_gid=gid, pw_dir=home)


ROOT = _user("root", 0, 0, "/root")


@pytest.fixture
def environment(tmp_path, monkeypatch):
    program = tmp_path / "opt/ai-device-client"
    (program / "runtime/bin").mkdir(parents=True)
    (program / "runtime/bin/python3").write_text("fixture")
    config_dir = tmp_path / "etc/ai-device-client"
    config_dir.mkdir(parents=True)
    token = config_dir / "token"
    token.write_text("x" * 32)
    config = config_dir / "client.json"
    config.write_text(json.dumps({"server_url": "ws://127.0.0.1:1", "token_file": str(token)}))
    unit = tmp_path / "etc/systemd/system/ai-device-client.service"
    unit.parent.mkdir(parents=True)
    state = tmp_path / "var/lib/ai-device-client"
    log = tmp_path / "var/log/ai-device-client"
    state.mkdir(parents=True)
    log.mkdir(parents=True)
    monkeypatch.setattr(registration, "PROGRAM", program)
    monkeypatch.setattr(registration, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(registration, "CONFIG", config)
    monkeypatch.setattr(registration, "UNIT_PATH", unit)
    monkeypatch.setattr(registration, "STATE_DIR", state)
    monkeypatch.setattr(registration, "LOG_DIR", log)
    # Conflict scanning inspects the live host; its own tests cover that.
    import device_runtime.client_conflicts as conflicts

    monkeypatch.setattr(conflicts, "check_conflicts", lambda: None)
    # The package lock coordinates with real dpkg files; its own tests cover it.
    from contextlib import contextmanager

    import device_runtime.client_management as management

    @contextmanager
    def fake_lock(*, package_script=False):
        yield

    monkeypatch.setattr(management, "management_lock", fake_lock)
    # Ownership would need root-owned parents; the calls are recorded instead.
    # The ownership rule itself is exercised by its own test.
    owned = []
    original_owner_check = registration._require_owner

    def record(path, uid, gid):
        owned.append((Path(path).name, uid, gid))

    monkeypatch.setattr(registration.os, "chown", record)
    monkeypatch.setattr(registration.os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(registration, "_require_owner", lambda info, allowed: None)
    enabled = []
    import device_runtime.client_service as service

    monkeypatch.setattr(service.SystemdClient, "enable", lambda self: enabled.append(True))
    return SimpleNamespace(
        program=program,
        config_dir=config_dir,
        config=config,
        unit=unit,
        token=token,
        state=state,
        log=log,
        owned=owned,
        enabled=enabled,
        original_owner_check=original_owner_check,
    )


def _accounts(monkeypatch, account):
    monkeypatch.setattr(registration.pwd, "getpwnam", lambda name: account)
    monkeypatch.setattr(registration.grp, "getgrgid", lambda gid: SimpleNamespace(gr_name="g"))
    monkeypatch.setattr(registration.os, "geteuid", lambda: 0)
    monkeypatch.setattr(registration, "service_active", lambda: False)


def test_root_is_an_explicit_choice_not_a_default(environment, monkeypatch):
    _accounts(monkeypatch, ROOT)
    name, kind = registration.register("root")
    assert (name, kind) == ("root", "root")
    assert "User=root" in environment.unit.read_text()


def test_ordinary_user_owns_runtime_and_credential(environment, monkeypatch):
    _accounts(monkeypatch, _user())
    name, kind = registration.register("device")
    assert (name, kind) == ("device", "user")
    assert "User=device" in environment.unit.read_text()
    owned = [(name, uid, gid) for name, uid, gid in environment.owned if (uid, gid) == (1234, 1234)]
    # The service user must be able to read the token and write both directories,
    # which are both named ai-device-client under different parents.
    assert ("token", 1234, 1234) in owned
    assert [name for name, _, _ in owned].count("ai-device-client") == 2


def test_registration_enables_the_unit_without_starting_it(environment, monkeypatch):
    _accounts(monkeypatch, _user())
    registration.register("device")
    assert environment.enabled == [True]


def test_existing_state_contents_are_aligned(environment, monkeypatch):
    (environment.state / "operations.sqlite3").write_text("x")
    _accounts(monkeypatch, _user())
    registration.register("device")
    aligned = [name for name, uid, gid in environment.owned if name == "operations.sqlite3"]
    assert aligned, "state contents must follow the service user"


def test_foreign_owner_is_refused(environment, monkeypatch):
    _accounts(monkeypatch, _user())
    monkeypatch.setattr(registration, "_require_owner", environment.original_owner_check)
    with pytest.raises(ValueError, match="owned by another user"):
        registration.register("device")


def test_returning_to_a_previous_user_is_allowed(environment, monkeypatch):
    """root -> device -> root must work: the unit names the previous owner."""
    _accounts(monkeypatch, _user())
    registration.register("device")
    environment.unit.write_text("[Service]\nUser=device\n")
    previous = registration._previous_user()
    assert previous is not None and previous.pw_uid == 1234
    allowed = registration._allowed_owners(ROOT, previous)
    assert previous.pw_uid in allowed
def test_unrelated_owner_is_not_allowed(environment):
    allowed = registration._allowed_owners(ROOT, None)
    assert 1234 not in allowed


def test_missing_user_is_refused(environment):
    with pytest.raises(ValueError):
        registration.register("")
    with pytest.raises(ValueError):
        registration.register("-oops")


def test_unknown_user_is_refused(environment, monkeypatch):
    def missing(name):
        raise KeyError(name)

    monkeypatch.setattr(registration.pwd, "getpwnam", missing)
    with pytest.raises(ValueError, match="does not exist"):
        registration.register("nosuchuser")


def test_registration_requires_root(environment, monkeypatch):
    monkeypatch.setattr(registration.os, "geteuid", lambda: 1000)
    with pytest.raises(PermissionError):
        registration.register("root")


def test_missing_configuration_is_refused(environment, monkeypatch):
    _accounts(monkeypatch, ROOT)
    environment.config.unlink()
    with pytest.raises(ValueError, match="configuration is missing"):
        registration.register("root")


def test_token_outside_configuration_directory_is_refused(environment, monkeypatch):
    _accounts(monkeypatch, _user())
    outside = environment.config_dir.parent / "token"
    outside.write_text("x" * 32)
    environment.config.write_text(json.dumps({"token_file": str(outside)}))
    with pytest.raises(ValueError, match="outside"):
        registration.register("device")


def test_running_service_cannot_change_user(environment, monkeypatch):
    _accounts(monkeypatch, _user())
    monkeypatch.setattr(registration, "service_active", lambda: True)
    environment.unit.write_text("[Service]\nUser=device\n")
    # A different, existing account holds the unit; registration must not move it.
    monkeypatch.setattr(
        registration, "_previous_user", lambda: _user("operator", 4242, 4242, "/home/operator")
    )
    with pytest.raises(ValueError, match="different user"):
        registration.register("device")


def test_unknown_service_state_blocks_registration(environment, monkeypatch):
    _accounts(monkeypatch, _user())
    environment.unit.write_text("[Service]\nUser=device\n")
    monkeypatch.setattr(
        registration, "_previous_user", lambda: _user("operator", 4242, 4242, "/home/operator")
    )
    monkeypatch.setattr(
        registration,
        "service_active",
        lambda: (_ for _ in ()).throw(ValueError("client service state is unknown")),
    )
    with pytest.raises(ValueError, match="unknown"):
        registration.register("device")


def test_registration_never_starts_the_service(environment, monkeypatch):
    _accounts(monkeypatch, _user())

    import device_runtime.client_service as service

    def forbidden(*a, **k):
        raise AssertionError("registration must not start or stop the service")

    monkeypatch.setattr(service.SystemdClient, "start", forbidden)
    monkeypatch.setattr(service.SystemdClient, "stop_for_install", forbidden)
    registration.register("device")
    assert environment.unit.is_file()
