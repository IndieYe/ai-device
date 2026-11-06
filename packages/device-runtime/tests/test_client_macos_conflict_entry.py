"""Read-only host entry contract; all discovery and privilege checks are substitutes."""

import pytest

from device_runtime import client_macos_conflicts as conflicts


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(conflicts.sys, "platform", "darwin")
    monkeypatch.setattr(conflicts.os, "geteuid", lambda: 0)
    calls = []
    monkeypatch.setattr(conflicts, "check_registration_conflicts", lambda **kw: calls.append(kw))
    return calls


def test_explicit_entry_success(host, capsys):
    assert conflicts.main(["--confirm-legacy-clients-disabled"]) == 0
    assert host == [{"confirm_legacy_clients_disabled": True}]
    assert capsys.readouterr() == ("AI_DEVICE_HOST_CONFLICTS_CLEAR_V1\n", "")


@pytest.mark.parametrize("platform,uid", [("linux", 0), ("darwin", 501)])
def test_entry_requires_native_administrator(host, monkeypatch, capsys, platform, uid):
    monkeypatch.setattr(conflicts.sys, "platform", platform)
    monkeypatch.setattr(conflicts.os, "geteuid", lambda: uid)
    assert conflicts.main(["--confirm-legacy-clients-disabled"]) == 70
    assert not host
    assert capsys.readouterr() == ("", "Mac host conflict check refused\n")


@pytest.mark.parametrize("args", [[], ["--user", "private-name"], ["--source", "/private/path"]])
def test_entry_missing_confirmation_or_unknown_arguments_do_not_scan(host, capsys, args):
    with pytest.raises(SystemExit) as exc:
        conflicts.main(args)
    assert exc.value.code == 2
    assert not host
    assert "AI_DEVICE_HOST_CONFLICTS_CLEAR_V1" not in capsys.readouterr().out


@pytest.mark.parametrize("error", [conflicts.MacClientConflict, OSError, RuntimeError])
def test_entry_failure_is_fixed_and_no_positive_token(host, monkeypatch, capsys, error):
    def failed(**kwargs):
        raise error("private home token and command must not escape")

    monkeypatch.setattr(conflicts, "check_registration_conflicts", failed)
    assert conflicts.main(["--confirm-legacy-clients-disabled"]) == 70
    assert capsys.readouterr() == ("", "Mac host conflict check refused\n")


def test_help_is_read_only(host, capsys):
    with pytest.raises(SystemExit) as exc:
        conflicts.main(["--help"])
    assert exc.value.code == 0
    assert not host
    assert "--confirm-legacy-clients-disabled" in capsys.readouterr().out
