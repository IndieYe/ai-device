import subprocess
import sys

import pytest

from device_runtime import client_macos_query as query


def child(code):
    return [sys.executable, "-I", "-c", code]


@pytest.mark.parametrize("size", [0, 1, query.LIMIT])
def test_capture_at_limit(size):
    result = query._capture(child(f'import os; os.write(1, b"x" * {size})'), timeout=5)
    assert result.returncode == 0
    assert result.stdout == b"x" * size


def test_over_limit_child_reaped(monkeypatch):
    created = []
    popen = subprocess.Popen

    def spawn(*a, **kw):
        process = popen(*a, **kw)
        created.append(process)
        return process

    monkeypatch.setattr(query.subprocess, "Popen", spawn)
    with pytest.raises(ValueError, match="capture limit"):
        query._capture(child('import os,time; os.write(1,b"x" * 400000); time.sleep(2)'), timeout=5)
    assert created[0].poll() is not None
    assert created[0].stdout.closed


@pytest.mark.parametrize(
    "code",
    [
        "import time; time.sleep(2)",
        "import os,time; os.close(1); time.sleep(2)",
    ],
)
def test_deadline_includes_pipe_and_exit(monkeypatch, code):
    created = []
    popen = subprocess.Popen

    def spawn(*a, **kw):
        process = popen(*a, **kw)
        created.append(process)
        return process

    monkeypatch.setattr(query.subprocess, "Popen", spawn)
    with pytest.raises(subprocess.TimeoutExpired):
        query._capture(child(code), timeout=0.2)
    assert created[0].poll() is not None
    assert created[0].stdout.closed


def test_nonzero_and_stderr_are_not_success():
    result = query._capture(
        child('import os; os.write(2,b"private"); raise SystemExit(7)'), timeout=5
    )
    assert result.returncode == 7
    assert result.stdout == b""


def test_public_runner_only_allows_read_query():
    with pytest.raises(ValueError):
        query.run_query(
            ["/bin/launchctl", "bootout", "gui/501/example"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )


def test_observer_uses_bounded_runner(monkeypatch):
    from pathlib import Path

    from device_runtime import client_macos_service_status as status
    from device_runtime.client_macos_layout import MacClientLayout

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        raise ValueError("private output")

    monkeypatch.setattr(status, "run_query", run)
    with pytest.raises(status.LaunchdStatusError) as caught:
        status.observe_service(MacClientLayout(501, Path("/Users/example")))
    assert calls == [["/bin/launchctl", "print", "gui/501/local.ai-device.client"]]
    assert "private" not in str(caught.value)


def test_query_cleanup_escalation_is_confined_to_created_child():
    # Fake process only: prove fallback is not routed through a service PID or
    # process group. No real signal-resistant child is needed for this branch.
    class Stream:
        closed = False

        def fileno(self):
            raise OSError("query pipe failed")

        def close(self):
            self.closed = True

    class Process:
        def __init__(self):
            self.stdout = Stream()
            self.calls = []

        def poll(self):
            return None

        def terminate(self):
            self.calls.append("terminate")

        def kill(self):
            self.calls.append("kill")

        def wait(self, timeout=None):
            self.calls.append(("wait", timeout))
            if "kill" not in self.calls:
                raise subprocess.TimeoutExpired("query", timeout)
            return -9

    from unittest.mock import patch

    process = Process()
    with patch.object(query.subprocess, "Popen", return_value=process), pytest.raises(OSError):
        query._capture(["unused"], timeout=1)
    assert process.calls == ["terminate", ("wait", 1), "kill", ("wait", 1)]
    assert process.stdout.closed


@pytest.mark.parametrize("during_spawn", [False, True])
def test_cooperative_cancel_reaps_finite_child(monkeypatch, during_spawn):
    import os

    created = []
    cancelled = False
    real_popen = subprocess.Popen

    def spawn(*args, **kwargs):
        nonlocal cancelled
        process = real_popen(*args, **kwargs)
        created.append(process)
        # Simulate signal while Popen has created its child but before caller
        # receives the handle. Cancellation records state, never interrupts.
        if during_spawn:
            cancelled = True
        return process

    def check():
        nonlocal cancelled
        if created:
            cancelled = True
        return cancelled

    monkeypatch.setattr(query.subprocess, "Popen", spawn)
    token = query._capture_cancelled.set(check)
    try:
        with pytest.raises(ValueError, match="cancelled"):
            query._capture(child("import time;time.sleep(3)"), timeout=5)
    finally:
        query._capture_cancelled.reset(token)
    assert len(created) == 1 and created[0].poll() is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(created[0].pid, os.WNOHANG)


def test_cancel_before_spawn_has_no_child(monkeypatch):
    monkeypatch.setattr(query.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    token = query._capture_cancelled.set(lambda: True)
    try:
        with pytest.raises(ValueError, match="cancelled"):
            query._capture(child("pass"), timeout=5)
    finally:
        query._capture_cancelled.reset(token)


@pytest.mark.parametrize(
    "timeout", [float("nan"), float("inf"), -float("inf"), True, False, None, "1", 0, -1, 10.01]
)
@pytest.mark.parametrize("kind", ["query", "kickstart", "capture"])
def test_invalid_timeout_never_spawns(monkeypatch, timeout, kind):
    monkeypatch.setattr(query.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    with pytest.raises(ValueError):
        if kind == "capture":
            query._capture(child("pass"), timeout=timeout)
        else:
            runner = query.run_query if kind == "query" else query.run_kickstart
            runner(
                [
                    "/bin/launchctl",
                    "print" if kind == "query" else "kickstart",
                    "gui/501/local.ai-device.client",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
