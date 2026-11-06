import os
from types import SimpleNamespace

import pytest

from device_runtime.client_macos_account import MacAccountError
from device_runtime.client_macos_entry import run_managed
from device_runtime.client_macos_lock import MacLockBusy, run_or_replace_lock


@pytest.fixture
def account(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    (directory / "runtime.lock").touch(mode=0o600)
    return SimpleNamespace(
        layout=SimpleNamespace(
            uid=os.getuid(), directory=directory, config=directory / "client.json"
        )
    )


def test_lock_covers_entire_invoke_and_cleanup(account):
    calls = []

    def invoke(argv):
        calls.append(argv)
        with (
            pytest.raises(MacLockBusy),
            run_or_replace_lock(account.layout.directory / "runtime.lock", uid=os.getuid()),
        ):
            pytest.fail("lock released before cleanup")

    run_managed(account.layout.config, account=account, invoke=invoke)
    assert calls == [["--config", str(account.layout.config)]]
    with run_or_replace_lock(account.layout.directory / "runtime.lock", uid=os.getuid()):
        pass


def test_busy_does_not_invoke_client(account):
    def forbidden(argv):
        pytest.fail("busy entry invoked client")

    with (
        run_or_replace_lock(account.layout.directory / "runtime.lock", uid=os.getuid()),
        pytest.raises(MacLockBusy),
    ):
        run_managed(account.layout.config, account=account, invoke=forbidden)


def test_failure_releases_without_swallowing_application_error(account):
    def failing(argv):
        raise RuntimeError("application failure")

    with pytest.raises(RuntimeError, match="application failure"):
        run_managed(account.layout.config, account=account, invoke=failing)
    with run_or_replace_lock(account.layout.directory / "runtime.lock", uid=os.getuid()):
        pass


def test_wrong_config_is_rejected_before_client(account):
    with pytest.raises(MacAccountError, match="registered"):
        run_managed("/wrong/config", account=account, invoke=lambda _: pytest.fail("called"))


def test_wrong_uid_rejected(account):
    account.layout.uid += 1
    with pytest.raises(MacAccountError, match="selected"):
        run_managed(account.layout.config, account=account)


def test_cli_busy_exits_normally_without_client(account, monkeypatch, capsys):
    from device_runtime import client_macos_entry as entry

    monkeypatch.setattr(entry.sys, "platform", "darwin")
    monkeypatch.setattr(entry.pwd, "getpwuid", lambda _: SimpleNamespace(pw_name="test"))
    monkeypatch.setattr(entry, "resolve_account", lambda _: account)
    with run_or_replace_lock(account.layout.directory / "runtime.lock", uid=os.getuid()):
        entry.main(["--config", str(account.layout.config)])
    assert "start deferred" in capsys.readouterr().err


def test_cli_missing_lock_fails_closed(account, monkeypatch, capsys):
    from device_runtime import client_macos_entry as entry

    monkeypatch.setattr(entry.sys, "platform", "darwin")
    monkeypatch.setattr(entry.pwd, "getpwuid", lambda _: SimpleNamespace(pw_name="test"))
    monkeypatch.setattr(entry, "resolve_account", lambda _: account)
    (account.layout.directory / "runtime.lock").unlink()
    with pytest.raises(SystemExit) as caught:
        entry.main(["--config", str(account.layout.config)])
    assert caught.value.code == 2
    assert str(account.layout.directory) not in capsys.readouterr().err


@pytest.mark.parametrize("outcome", ["success", "failure", "wrong_config"])
def test_inherited_lock_covers_invoke_and_failure(account, outcome):
    import fcntl

    fd = os.open(account.layout.directory / "runtime.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.set_inheritable(fd, True)
    calls = []

    def invoke(argv):
        calls.append(argv)
        assert not os.get_inheritable(fd)
        with (
            pytest.raises(MacLockBusy),
            run_or_replace_lock(account.layout.directory / "runtime.lock", uid=os.getuid()),
        ):
            pytest.fail("lock released during invoke")
        if outcome == "failure":
            raise RuntimeError("application failure")

    config = "/wrong" if outcome == "wrong_config" else account.layout.config
    if outcome == "success":
        run_managed(config, account=account, invoke=invoke, inherited_descriptor=fd)
    else:
        error = MacAccountError if outcome == "wrong_config" else RuntimeError
        with pytest.raises(error):
            run_managed(config, account=account, invoke=invoke, inherited_descriptor=fd)
    assert len(calls) == (0 if outcome == "wrong_config" else 1)
    with pytest.raises(OSError):
        os.fstat(fd)
    with run_or_replace_lock(account.layout.directory / "runtime.lock", uid=os.getuid()):
        pass


def test_inherited_unheld_fd_has_no_reopen_fallback(account):
    from device_runtime.client_macos_lock import MacLockError

    fd = os.open(account.layout.directory / "runtime.lock", os.O_RDWR)
    with pytest.raises(MacLockError):
        run_managed(
            account.layout.config,
            account=account,
            inherited_descriptor=fd,
            invoke=lambda _: pytest.fail("invalid handoff invoked client"),
        )
    with pytest.raises(OSError):
        os.fstat(fd)


def test_cli_explicit_handoff_selects_fixed_fd(account, monkeypatch):
    from device_runtime import client_macos_entry as entry

    monkeypatch.setattr(entry.sys, "platform", "darwin")
    monkeypatch.setattr(entry.pwd, "getpwuid", lambda _: SimpleNamespace(pw_name="test"))
    monkeypatch.setattr(entry, "resolve_account", lambda _: account)
    calls = []
    monkeypatch.setattr(entry.os, "set_inheritable", lambda fd, value: None)
    monkeypatch.setattr(entry, "run_managed", lambda *a, **k: calls.append(k))
    entry.main(["--config", str(account.layout.config), "--inherit-runtime-lock"])
    assert calls == [{"account": account, "inherited_descriptor": 3}]


def test_cli_account_failure_closes_pending_handoff(account, monkeypatch):
    from device_runtime import client_macos_entry as entry

    events = []
    monkeypatch.setattr(entry.sys, "platform", "darwin")
    monkeypatch.setattr(
        entry.os, "set_inheritable", lambda fd, value: events.append(("cloexec", fd, value))
    )
    monkeypatch.setattr(entry.os, "close", lambda fd: events.append(("close", fd)))

    def failed(_):
        assert events == [("cloexec", 3, False)]
        raise KeyError("account missing")

    monkeypatch.setattr(entry.pwd, "getpwuid", failed)
    with pytest.raises(SystemExit) as caught:
        entry.main(["--config", str(account.layout.config), "--inherit-runtime-lock"])
    assert caught.value.code == 2
    assert events == [("cloexec", 3, False), ("close", 3)]


def test_default_managed_invoke_opts_into_lease_exit(account, monkeypatch):
    from device_runtime import client_cli

    calls = []
    monkeypatch.setattr(client_cli, "main", lambda argv, **kwargs: calls.append((argv, kwargs)))
    run_managed(account.layout.config, account=account)
    assert calls == [(["--config", str(account.layout.config)], {"lease_exit": True})]
