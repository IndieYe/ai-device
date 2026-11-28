from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from device_runtime import client_service


def reply(active="inactive", sub="dead", pid=0, control=0):
    return SimpleNamespace(
        returncode=0,
        stdout=(
            f"LoadState=loaded\nActiveState={active}\nSubState={sub}\nMainPID={pid}\nControlPID={control}\n"
        ),
    )


@pytest.mark.parametrize(
    "value",
    [
        reply("activating", "start", 1),
        reply("inactive", pid=1),
        reply("failed", control=5),
        reply("active", "running", 0),
    ],
)
def test_unknown_state_refuses(value):
    service = client_service.SystemdClient(run=lambda *a, **k: value)
    with pytest.raises(client_service.ServiceError):
        service.state()


def test_stop_holds_lease_until_confirmation(monkeypatch):
    held = []
    events = []
    responses = iter([reply("active", "running", 42), reply(), reply()])

    @contextmanager
    def lease(path):
        held.append(True)
        try:
            yield
        finally:
            held.clear()

    def run(argv, **kwargs):
        events.append(argv[1])
        if len(events) > 1:
            assert held
        return next(responses)

    monkeypatch.setattr(client_service, "idle_lease", lease)
    client_service.SystemdClient(run=run).stop_for_install("socket")
    assert events == ["show", "stop", "show"]
    assert not held


def test_stopped_does_not_require_socket(monkeypatch):
    monkeypatch.setattr(client_service, "idle_lease", lambda _: pytest.fail("unexpected lease"))
    client_service.SystemdClient(run=lambda argv, **k: reply()).stop_for_install("socket")


@pytest.mark.parametrize(
    "user", ["", None, "a\nExecStart=/bin/false", "name with space", "../root"]
)
def test_unit_rejects_ambiguous_user(user):
    with pytest.raises(ValueError):
        client_service.render_unit(user)


def test_unit_uses_explicit_user_and_never_forces_stop():
    unit = client_service.render_unit("device")
    assert "User=device\n" in unit
    assert "SendSIGKILL=no\n" in unit
    assert "TimeoutStopSec=30\n" in unit
    assert " -I -m device_runtime.client_cli --config " in unit
    assert "StateDirectoryMode=0700" in unit
    assert "StandardError=append:/var/log/ai-device-client/client.log" in unit
    assert "DEVICE_RUNTIME_TOKEN" not in unit


def test_stop_failure_releases_lease(monkeypatch):
    held = []

    @contextmanager
    def lease(path):
        held.append(True)
        try:
            yield
        finally:
            held.clear()

    def run(argv, **kwargs):
        if argv[1] == "show":
            return reply("active", "running", 42)
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(client_service, "idle_lease", lease)
    with pytest.raises(client_service.ServiceError):
        client_service.SystemdClient(run=run).stop_for_install("socket")
    assert not held


def test_explicit_start_clears_prior_start_limit():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[1])
        return reply("active", "running", 42)

    client_service.SystemdClient(run=run).start()
    assert calls == ["daemon-reload", "reset-failed", "start", "show"]


def test_configuration_probe_runs_as_service_user():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "--property=User" in argv:
            assert "--value" not in argv
            return SimpleNamespace(returncode=0, stdout="User=device\n")
        return SimpleNamespace(returncode=0, stdout="")

    client_service.SystemdClient(run=run).validate_config()
    assert calls[1][:4] == ["runuser", "-u", "device", "--"]


@pytest.mark.parametrize("outcome", ["timeout", "still-running"])
def test_stop_timeout_or_unconfirmed_stop_releases_lease(monkeypatch, outcome):
    import subprocess

    held = []
    calls = []

    @contextmanager
    def lease(path):
        held.append(True)
        try:
            yield
        finally:
            held.clear()

    def run(argv, **kwargs):
        calls.append(argv[1])
        if argv[1] == "stop":
            assert held
            if outcome == "timeout":
                raise subprocess.TimeoutExpired(argv, 45)
            return SimpleNamespace(returncode=0, stdout="")
        return reply("active", "running", 42)

    monkeypatch.setattr(client_service, "idle_lease", lease)
    with pytest.raises((subprocess.TimeoutExpired, client_service.ServiceError)):
        client_service.SystemdClient(run=run).stop_for_install("socket")
    assert not held
    assert calls == (["show", "stop"] if outcome == "timeout" else ["show", "stop", "show"])


def test_configuration_probe_enforces_installed_state_path():
    def run(argv, **kwargs):
        if argv[0] == "systemctl":
            return SimpleNamespace(returncode=0, stdout="User=device\n")
        assert "assert str(a.state_dir)=='/var/lib/ai-device-client'" in argv[-1]
        return SimpleNamespace(returncode=1, stdout="")

    with pytest.raises(client_service.ServiceError, match="configuration"):
        client_service.SystemdClient(run=run).validate_config()
