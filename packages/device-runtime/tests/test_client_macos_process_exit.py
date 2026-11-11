import subprocess
import sys
import threading

import psutil
import pytest

from device_runtime.client_macos_processes import (
    ProcessEvidenceError,
    ProcessIdentity,
    inspect_identity,
    wait_original_exit,
)

ORIGINAL = ProcessIdentity(123, 10.0)


@pytest.mark.parametrize("current", [None, ProcessIdentity(123, 11.0)])
def test_absence_or_reuse_confirms_only_original_gone(current):
    assert wait_original_exit(ORIGINAL, inspect=lambda _: current) is None


@pytest.mark.parametrize("current", ["unknown", ProcessIdentity(124, 10.0)])
def test_invalid_observation_never_confirms_exit(current):
    with pytest.raises(ProcessEvidenceError):
        wait_original_exit(ORIGINAL, inspect=lambda _: current)


def test_deadline_does_not_send_signal_or_wait_indefinitely():
    now = [0.0]
    sleeps = []

    def sleep(value):
        sleeps.append(value)
        now[0] += value

    with pytest.raises(ProcessEvidenceError, match="deadline"):
        wait_original_exit(
            ORIGINAL,
            timeout=0.12,
            interval=0.05,
            inspect=lambda _: ORIGINAL,
            clock=lambda: now[0],
            sleep=sleep,
        )
    assert sleeps == pytest.approx([0.05, 0.05, 0.02])


def test_slow_inspection_past_deadline_is_not_success():
    now = [0.0]

    def inspect(_):
        now[0] = 3

    with pytest.raises(ProcessEvidenceError, match="deadline"):
        wait_original_exit(ORIGINAL, timeout=1, inspect=inspect, clock=lambda: now[0])


@pytest.mark.parametrize("bad", [0, -1, True, float("inf"), float("nan")])
def test_invalid_deadline_rejected(bad):
    with pytest.raises(ProcessEvidenceError):
        wait_original_exit(ORIGINAL, timeout=bad, inspect=lambda _: pytest.fail("inspected"))


def test_permission_error_is_unknown(monkeypatch):
    def denied(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(psutil, "Process", denied)
    with pytest.raises(ProcessEvidenceError, match="unavailable"):
        inspect_identity(123)


def test_no_such_process_is_absence(monkeypatch):
    def missing(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", missing)
    assert inspect_identity(123) is None


def test_identity_changes_during_read_rejected(monkeypatch):
    from types import SimpleNamespace

    times = iter([10.0, 11.0])
    monkeypatch.setattr(
        psutil, "Process", lambda _: SimpleNamespace(create_time=lambda: next(times))
    )
    with pytest.raises(ProcessEvidenceError, match="changed"):
        inspect_identity(123)


def test_finite_natural_exit_observed_without_signals():
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(.4)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    identity = inspect_identity(child.pid)
    assert identity is not None
    # Parent reaps the child naturally; observer does not treat a zombie as a
    # completed disappearance, and does not own process termination.
    reaper = threading.Thread(target=lambda: child.wait(timeout=5))
    reaper.start()
    try:
        wait_original_exit(identity, timeout=5)
        assert child.returncode == 0
    finally:
        reaper.join(timeout=6)
        child.wait(timeout=5)
    assert not reaper.is_alive()
