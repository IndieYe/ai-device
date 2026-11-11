import json

import pytest

from device_runtime import client_macos_register as reg


@pytest.mark.parametrize(
    "action,target", [("register", "register"), ("resume", "resume_registration")]
)
def test_dispatch_explicit_action(monkeypatch, capsys, action, target):
    calls = []

    def operation(user, **kwargs):
        calls.append((user, kwargs))
        return {"registered": True, "started": False}

    monkeypatch.setattr(reg, target, operation)
    reg.main([action, "--user", "alice", "--confirm-legacy-clients-disabled"])
    assert calls == [("alice", {"confirm_legacy_clients_disabled": True})]
    assert json.loads(capsys.readouterr().out)["started"] is False


@pytest.mark.parametrize(
    "argv",
    [
        ["register"],
        ["register", "--user", "alice"],
        ["stop", "--user", "alice", "--confirm-legacy-clients-disabled"],
    ],
)
def test_invalid_arguments_never_execute(monkeypatch, argv):
    monkeypatch.setattr(reg, "register", lambda *a, **k: pytest.fail("executed"))
    with pytest.raises(SystemExit) as caught:
        reg.main(argv)
    assert caught.value.code == 2


@pytest.mark.parametrize(
    "error", [ValueError("credential"), RuntimeError("secret"), OSError("private")]
)
def test_failure_output_sanitized(monkeypatch, capsys, error):
    def operation(*a, **k):
        raise error

    monkeypatch.setattr(reg, "register", operation)
    with pytest.raises(SystemExit) as caught:
        reg.main(["register", "--user", "alice", "--confirm-legacy-clients-disabled"])
    output = capsys.readouterr()
    assert caught.value.code == 2
    assert not output.out
    assert str(error) not in output.err
    assert "incomplete" in output.err
