from types import SimpleNamespace

import pytest

from device_runtime import native_bootstrap as bootstrap


@pytest.fixture
def child_api(monkeypatch):
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap.os, "P_PIDFD", 3, raising=False)
    monkeypatch.setattr(bootstrap.os, "WNOWAIT", 0x1000000, raising=False)
    monkeypatch.setattr(bootstrap.signal, "pidfd_send_signal", lambda *_args: None, raising=False)


def test_reap_live_child_signals_pidfd_and_returns_wait_evidence(child_api, monkeypatch):
    result = SimpleNamespace(si_pid=123, si_code=2, si_status=9)
    calls = []
    def waitid(kind, fd, flags):
        calls.append((kind, fd, flags))
        return None if flags & bootstrap.os.WNOWAIT else result
    monkeypatch.setattr(bootstrap.os, "waitid", waitid)
    monkeypatch.setattr(bootstrap.signal, "pidfd_send_signal", lambda *args: calls.append(args))
    assert bootstrap._reap_direct_child_pidfd(7, deadline=bootstrap.time.monotonic()+5) == (123, 2, 9)
    assert calls[0][2] & bootstrap.os.WNOWAIT
    assert calls[1][:2] == (7, bootstrap.signal.SIGKILL)
    assert calls[2][2] & bootstrap.os.WNOHANG


def test_reap_already_exited_child_does_not_signal(child_api, monkeypatch):
    result = SimpleNamespace(si_pid=123, si_code=1, si_status=0)
    monkeypatch.setattr(bootstrap.os, "waitid", lambda *_args: result)
    monkeypatch.setattr(bootstrap.signal, "pidfd_send_signal", lambda *_args: pytest.fail("already exited"))
    assert bootstrap._reap_direct_child_pidfd(7, deadline=bootstrap.time.monotonic()+5) == (123, 1, 0)


def test_reap_rejects_non_child_before_signal(child_api, monkeypatch):
    def reject(*_args):
        raise ChildProcessError("not our child")
    monkeypatch.setattr(bootstrap.os, "waitid", reject)
    monkeypatch.setattr(bootstrap.signal, "pidfd_send_signal", lambda *_args: pytest.fail("must not signal"))
    with pytest.raises(bootstrap.BootstrapError, match="direct child"):
        bootstrap._reap_direct_child_pidfd(7, deadline=bootstrap.time.monotonic()+5)


@pytest.mark.parametrize("pidfd,deadline", [(True, 10.0), (-1, 10.0), (7, float('nan')), (7, float('inf'))])
def test_reap_rejects_invalid_input(child_api, pidfd, deadline):
    with pytest.raises(bootstrap.BootstrapError, match="inputs"):
        bootstrap._reap_direct_child_pidfd(pidfd, deadline=deadline)


def test_reap_times_out_without_blocking_wait(child_api, monkeypatch):
    monkeypatch.setattr(bootstrap.os, "waitid", lambda *_args: None)
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: 10.0)
    with pytest.raises(bootstrap.BootstrapError, match="deadline"):
        bootstrap._reap_direct_child_pidfd(7, deadline=9.0)


def test_reap_child_exit_during_signal_is_reaped(child_api, monkeypatch):
    result = SimpleNamespace(si_pid=123, si_code=1, si_status=4)
    results = iter([None, result])
    monkeypatch.setattr(bootstrap.os, "waitid", lambda *_args: next(results))
    def exited(*_args):
        raise ProcessLookupError("already gone")
    monkeypatch.setattr(bootstrap.signal, "pidfd_send_signal", exited)
    assert bootstrap._reap_direct_child_pidfd(7, deadline=bootstrap.time.monotonic()+5) == (123, 1, 4)


def test_reap_fails_closed_when_child_remains_unreaped(child_api, monkeypatch):
    monkeypatch.setattr(bootstrap.os, "waitid", lambda *_args: None)
    clock = iter([1.0, 2.0])
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: next(clock))
    with pytest.raises(bootstrap.BootstrapError, match="deadline"):
        bootstrap._reap_direct_child_pidfd(7, deadline=1.5)


def test_reap_reports_signal_failure_without_assuming_exit(child_api, monkeypatch):
    monkeypatch.setattr(bootstrap.os, "waitid", lambda *_args: None)
    def fail(*_args):
        raise PermissionError("blocked")
    monkeypatch.setattr(bootstrap.signal, "pidfd_send_signal", fail)
    with pytest.raises(bootstrap.BootstrapError, match="pidfd reap failed"):
        bootstrap._reap_direct_child_pidfd(7, deadline=bootstrap.time.monotonic()+5)
