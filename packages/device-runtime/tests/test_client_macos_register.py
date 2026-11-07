from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from device_runtime import client_macos_register as reg


@pytest.fixture
def flow(monkeypatch):
    events = []
    account = SimpleNamespace(
        layout=SimpleNamespace(
            uid=501, directory=Path("/Users/test/config"), plist=Path("/fixture/agent.plist")
        ),
        gid=20,
    )
    monkeypatch.setattr(reg.sys, "platform", "darwin")
    monkeypatch.setattr(reg.os, "geteuid", lambda: 0)

    @contextmanager
    def lock():
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")

    monkeypatch.setattr(reg, "management_lock", lock)

    def resolve(user):
        events.append("resolve")
        assert user == "test"
        return account

    monkeypatch.setattr(reg, "resolve_account", resolve)
    for name in (
        "check_registration_conflicts",
        "validate_launcher",
        "validate_registration_files",
        "validate_config_as_user",
        "reserve_identity",
        "prepare_user_directories",
        "provision_runtime_lock",
        "provision_start_gate",
    ):
        monkeypatch.setattr(reg, name, lambda *args, _name=name, **kwargs: events.append(_name))

    def publish(target):
        assert target is account
        events.append("publish")
        return Path("/fixture/agent.plist")

    monkeypatch.setattr(reg, "publish_agent", publish)
    return events


def test_order_and_no_start(flow):
    result = reg.register("test", confirm_legacy_clients_disabled=True)
    assert flow == [
        "lock",
        "resolve",
        "check_registration_conflicts",
        "validate_launcher",
        "validate_registration_files",
        "validate_config_as_user",
        "reserve_identity",
        "prepare_user_directories",
        "provision_runtime_lock",
        "provision_start_gate",
        "check_registration_conflicts",
        "publish",
        "unlock",
    ]
    assert result["started"] is False
    assert result["uid"] == 501


@pytest.mark.parametrize(
    "stage",
    [
        "check_registration_conflicts",
        "validate_launcher",
        "validate_registration_files",
        "validate_config_as_user",
        "reserve_identity",
        "prepare_user_directories",
        "provision_runtime_lock",
        "provision_start_gate",
    ],
)
def test_failure_before_publish_never_registers(flow, monkeypatch, stage):
    def fail(*args, **kwargs):
        raise ValueError("injected")

    monkeypatch.setattr(reg, stage, fail)
    with pytest.raises(ValueError, match="injected"):
        reg.register("test", confirm_legacy_clients_disabled=True)
    assert "publish" not in flow
    assert flow[-1] == "unlock"


def test_confirmation_before_lock(flow):
    with pytest.raises(ValueError, match="confirm"):
        reg.register("test")
    assert flow == []


def test_non_admin_before_lock(flow, monkeypatch):
    monkeypatch.setattr(reg.os, "geteuid", lambda: 501)
    with pytest.raises(ValueError, match="administrator"):
        reg.register("test", confirm_legacy_clients_disabled=True)
    assert flow == []


def test_registration_real_files_in_isolated_home(tmp_path, monkeypatch):
    import os
    import plistlib

    from device_runtime.client_macos_layout import MacClientLayout
    from device_runtime.client_macos_management import management_lock

    uid, gid = os.getuid(), os.getgid()
    layout = MacClientLayout(uid, tmp_path / "home")
    layout.directory.mkdir(parents=True, mode=0o700)
    account = SimpleNamespace(layout=layout, gid=gid)
    monkeypatch.setattr(reg.sys, "platform", "darwin")
    monkeypatch.setattr(reg.os, "geteuid", lambda: 0)
    monkeypatch.setattr(reg, "resolve_account", lambda user: account)
    monkeypatch.setattr(
        reg,
        "management_lock",
        lambda: management_lock(tmp_path / "management", owner_uid=uid, owner_gid=gid),
    )
    monkeypatch.setattr(reg, "check_registration_conflicts", lambda **kwargs: None)
    monkeypatch.setattr(reg, "validate_launcher", lambda: None)
    monkeypatch.setattr(reg, "validate_registration_files", lambda account: None)
    monkeypatch.setattr(reg, "validate_config_as_user", lambda account: None)
    monkeypatch.setattr(reg, "reserve_identity", lambda account: None)
    result = reg.register("test", confirm_legacy_clients_disabled=True)
    assert result["registered"] and result["started"] is False
    assert plistlib.loads(layout.plist.read_bytes()) == layout.definition()
    lock = layout.directory / "runtime.lock"
    before = lock.stat().st_ino
    from device_runtime.client_macos_plist import PlistPublishError

    with pytest.raises(PlistPublishError):
        reg.register("test", confirm_legacy_clients_disabled=True)
    assert lock.stat().st_ino == before
    assert layout.state.is_dir()


@pytest.mark.parametrize("resume", [False, True])
def test_long_socket_rejected_before_identity_or_user_files(flow, monkeypatch, resume):
    from device_runtime.client_macos_account import MacAccountError, validate_registration_files
    from device_runtime.client_macos_layout import MacClientLayout

    account = SimpleNamespace(
        name="test", gid=20, layout=MacClientLayout(501, Path("/Users/" + "a" * 80))
    )
    monkeypatch.setattr(reg, "resolve_account", lambda user: account)
    identity = {
        "schema_version": 1,
        "user": "test",
        "uid": 501,
        "gid": 20,
        "home": str(account.layout.home),
    }
    monkeypatch.setattr(reg, "read_identity", lambda: identity.copy())
    monkeypatch.setattr(reg, "validate_registration_files", validate_registration_files)
    operation = reg.resume_registration if resume else reg.register
    with pytest.raises(MacAccountError, match="socket path"):
        operation("test", confirm_legacy_clients_disabled=True)
    assert flow == ["lock", "check_registration_conflicts", "validate_launcher", "unlock"]
    assert identity["home"] == str(account.layout.home)


@pytest.mark.parametrize("fail_publish", [False, True])
def test_configured_user_registration_and_explicit_resume(tmp_path, monkeypatch, fail_publish):
    import os
    import plistlib

    from device_runtime.client_config import initialize, load
    from device_runtime.client_macos_identity import read_identity, reserve_identity
    from device_runtime.client_macos_layout import MacClientLayout
    from device_runtime.client_macos_management import management_lock

    uid, gid = os.getuid(), os.getgid()
    layout = MacClientLayout(uid, tmp_path / "home")
    layout.directory.parent.mkdir(parents=True, mode=0o700)
    account = SimpleNamespace(name="test", layout=layout, gid=gid)
    management = tmp_path / "management"
    monkeypatch.setattr(reg, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(reg.sys, "platform", "darwin")
    monkeypatch.setattr(reg, "resolve_account", lambda user: account)
    monkeypatch.setattr(reg, "management_lock", lambda: management_lock(management, owner_uid=uid, owner_gid=gid))
    monkeypatch.setattr(reg, "check_registration_conflicts", lambda **kwargs: None)
    monkeypatch.setattr(reg, "validate_launcher", lambda: None)
    # Temporary ancestors and account access are adapters, not host/root validation.
    monkeypatch.setattr(reg, "validate_registration_files", lambda account: None)

    def validate_config(account):
        args, token = load(account.layout.config)
        assert args.state_dir == account.layout.state
        assert len(token) >= 16

    monkeypatch.setattr(reg, "validate_config_as_user", validate_config)
    monkeypatch.setattr(reg, "reserve_identity", lambda account: reserve_identity(account, directory=management))
    monkeypatch.setattr(reg, "read_identity", lambda: read_identity(directory=management))
    with pytest.raises(ValueError, match="configuration or private token"):
        reg.register("test", confirm_legacy_clients_disabled=True)
    assert not (management / "registration.json").exists()
    assert not layout.plist.exists()
    initialize(layout.directory, server_url="wss://example.test", device_id="first-client",
               state_dir=layout.state, token="test-only-client-secret")
    config_before, token_before = layout.config.read_bytes(), layout.token.read_bytes()
    publish = reg.publish_agent
    if fail_publish:
        def unavailable(layout):
            raise OSError("test publication unavailable")
        monkeypatch.setattr(reg, "publish_agent", unavailable)
        with pytest.raises(OSError, match="publication unavailable"):
            reg.register("test", confirm_legacy_clients_disabled=True)
        identity_before = (management / "registration.json").read_bytes()
        assert not layout.plist.exists()
        lock_inodes = [(layout.directory / name).stat().st_ino for name in ("runtime.lock", "start.lock")]
        monkeypatch.setattr(reg, "publish_agent", publish)
        result = reg.resume_registration("test", confirm_legacy_clients_disabled=True)
        assert (management / "registration.json").read_bytes() == identity_before
        assert [(layout.directory / name).stat().st_ino for name in ("runtime.lock", "start.lock")] == lock_inodes
    else:
        result = reg.register("test", confirm_legacy_clients_disabled=True)
    assert result == {"registered": True, "started": False, "uid": uid, "plist": str(layout.plist)}
    assert read_identity(directory=management)["home"] == str(layout.home)
    assert plistlib.loads(layout.plist.read_bytes()) == layout.definition()
    assert layout.config.read_bytes() == config_before
    assert layout.token.read_bytes() == token_before


def test_cli_reports_the_actual_refusal_reason(monkeypatch, capsys):
    """The operator needs the cause, not a generic "inspect state".

    An existing valid registration and a stale definition owned by someone
    else require opposite actions; collapsing both into one line made the
    message useless.
    """

    from device_runtime.client_macos_account import MacAccountError

    def refuse(*args, **kwargs):
        raise MacAccountError("LaunchAgent already exists for this user; create-only")

    monkeypatch.setattr(reg, "register", refuse)
    with pytest.raises(SystemExit) as exit_info:
        reg.main(["register", "--user", "test", "--confirm-legacy-clients-disabled"])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "create-only" in err
    assert "inspect registration state" not in err


def test_cli_withholds_text_from_unexpected_exceptions(monkeypatch, capsys):
    """A bare OSError can quote a path; its text must not reach the operator."""

    def refuse(*args, **kwargs):
        raise OSError("/Users/alice/secret/token")

    monkeypatch.setattr(reg, "register", refuse)
    with pytest.raises(SystemExit):
        reg.main(["register", "--user", "test", "--confirm-legacy-clients-disabled"])
    err = capsys.readouterr().err
    assert "secret" not in err
    assert "inspect registration state" in err


def test_cli_keeps_generic_wording_for_empty_diagnostic(monkeypatch, capsys):
    from device_runtime.client_macos_account import MacAccountError

    def refuse(*args, **kwargs):
        raise MacAccountError("")

    monkeypatch.setattr(reg, "register", refuse)
    with pytest.raises(SystemExit):
        reg.main(["register", "--user", "test", "--confirm-legacy-clients-disabled"])
    assert "inspect registration state" in capsys.readouterr().err


@pytest.mark.parametrize(
    "error,needle",
    [
        (
            "conflict",
            (
                "already registered for this user; registration is create-only, "
                "so use the existing definition or retire it first"
            ),
        ),
        ("identity", "registered account changed; automatic rebinding refused"),
    ],
)
def test_cli_reports_conflict_and_identity_diagnostics(monkeypatch, capsys, error, needle):
    """These two are the refusals an operator actually hits on a used machine.

    They are covered with their real exception types because the safe-to-print
    allowlist is by type: dropping either type from it would silently degrade
    the message back to the generic wording.
    """
    from device_runtime.client_macos_conflicts import MacClientConflict
    from device_runtime.client_macos_identity import MacIdentityError

    builder = {"conflict": MacClientConflict, "identity": MacIdentityError}[error]

    def refuse(*args, **kwargs):
        raise builder(needle)

    monkeypatch.setattr(reg, "register", refuse)
    with pytest.raises(SystemExit):
        reg.main(["register", "--user", "test", "--confirm-legacy-clients-disabled"])
    err = capsys.readouterr().err
    assert needle in err
    assert "inspect registration state" not in err
