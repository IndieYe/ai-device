"""No real launchctl calls; exact no-k start and refusal contracts."""

import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from device_runtime import client_macos_query as query
from device_runtime import client_macos_start as start


@pytest.fixture
def context(monkeypatch):
    state = {"held": False, "calls": 0}
    account = SimpleNamespace(layout=SimpleNamespace(uid=501))

    @contextmanager
    def lock():
        state["held"] = True
        try:
            yield
        finally:
            state["held"] = False

    monkeypatch.setattr(start.sys, "platform", "darwin")
    monkeypatch.setattr(start.os, "geteuid", lambda: 0)
    monkeypatch.setattr(start, "management_lock", lock)
    monkeypatch.setattr(start, "management_lock_matches", lambda fd: state["held"])
    monkeypatch.setattr(start, "registered_account", lambda: account)
    monkeypatch.setattr(start, "validate_agent", lambda a: None)

    def observe(layout, **kwargs):
        assert state["held"] and kwargs["require_fixed_entry"] is True
        return SimpleNamespace(target="gui/501/local.ai-device.client")

    monkeypatch.setattr(start, "observe_service", observe)
    return state


@pytest.mark.parametrize("case", ["clear", "nonzero", "timeout", "identity-change"])
def test_no_signal_start(context, monkeypatch, case):
    def run(argv, **kwargs):
        assert context["held"]
        assert argv == ["/bin/launchctl", "kickstart", "gui/501/local.ai-device.client"]
        context["calls"] += 1
        if case == "timeout":
            raise subprocess.TimeoutExpired("private", 10)
        if case == "identity-change":
            monkeypatch.setattr(start, "registered_account", lambda: None)
        return subprocess.CompletedProcess(argv, 1 if case == "nonzero" else 0, b"")

    if case == "clear":
        assert start.request_registered_start(run=run) == {
            "start_requested": True,
            "ready_verified": False,
        }
    else:
        with pytest.raises(start.MacStartError):
            start.request_registered_start(run=run)
    assert context["calls"] == 1 and not context["held"]


@pytest.mark.parametrize("case", ["unprivileged", "unknown", "wrong-target"])
def test_refusal_before_action(context, monkeypatch, case):
    if case == "unprivileged":
        monkeypatch.setattr(start.os, "geteuid", lambda: 501)
    else:

        def observe(*args, **kwargs):
            if case == "unknown":
                raise ValueError("unknown")
            return SimpleNamespace(target="gui/502/local.ai-device.client")

        monkeypatch.setattr(start, "observe_service", observe)

    def forbidden(*args, **kwargs):
        pytest.fail("must not start")

    with pytest.raises((start.MacStartError, ValueError)):
        start.request_registered_start(run=forbidden)
    assert not context["held"]


@pytest.mark.parametrize(
    "argv",
    [
        ["/bin/launchctl", "kickstart", "-k", "gui/501/local.ai-device.client"],
        ["/bin/launchctl", "bootout", "gui/501/local.ai-device.client"],
        ["/bin/launchctl", "kickstart", "system/local.ai-device.client"],
        ["/bin/launchctl", "kickstart", "gui/501/other"],
    ],
)
def test_runner_refuses_other_actions(argv):
    with pytest.raises(ValueError):
        query.run_kickstart(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )


def test_runner_exact_contract(monkeypatch):
    monkeypatch.setattr(query, "_capture", lambda argv, **kwargs: (argv, kwargs))
    argv = ["/bin/launchctl", "kickstart", "gui/501/local.ai-device.client"]
    assert query.run_kickstart(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    ) == (argv, {"timeout": 10})


@pytest.mark.parametrize("invalid_at", [1, 2, 3, 4, 5])
def test_caller_guard_loss_short_circuits_without_replay(context, invalid_at):
    context["held"] = True
    checks = 0

    def guard():
        nonlocal checks
        checks += 1
        return checks != invalid_at

    def run(argv, **kwargs):
        context["calls"] += 1
        return subprocess.CompletedProcess(argv, 0, b"")

    with pytest.raises(start.MacStartError, match="guard"):
        start._request_registered_start_held(guard_current=guard, run=run)
    assert checks == invalid_at
    assert context["calls"] == (1 if invalid_at >= 4 else 0)
    assert context["held"]  # Caller retains responsibility; core never unlocks.


def test_caller_guard_must_be_exact_true(context):
    context["held"] = True
    with pytest.raises(start.MacStartError, match="guard"):
        start._request_registered_start_held(guard_current=lambda: 1)
    assert context["calls"] == 0


@pytest.mark.parametrize("fault", ["nonzero", "timeout", "oserror", "encoding", "truncated"])
def test_real_observation_failure_never_requests_start(context, monkeypatch, fault):
    from device_runtime.client_macos_launchd_status import LaunchdStatusError
    from device_runtime.client_macos_service_status import observe_service

    monkeypatch.setattr(start, "observe_service", observe_service)
    queries = []

    def query(argv, **kwargs):
        queries.append(argv)
        assert context["held"]
        assert argv == ["/bin/launchctl", "print", "gui/501/local.ai-device.client"]
        assert kwargs["timeout"] == 10 and kwargs["check"] is False
        if fault == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        if fault == "oserror":
            raise OSError("query unavailable")
        return subprocess.CompletedProcess(
            argv,
            1 if fault == "nonzero" else 0,
            b"\xff" if fault == "encoding" else b"gui/501/local.ai-device.client = {\n",
        )

    def forbidden(*args, **kwargs):
        pytest.fail("unknown GUI service must never request start")

    with pytest.raises(LaunchdStatusError):
        start.request_registered_start(run=forbidden, observe_run=query)
    assert len(queries) == 1
    assert context["calls"] == 0 and not context["held"]
