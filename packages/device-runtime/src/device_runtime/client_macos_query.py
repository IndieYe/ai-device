"""Bounded capture for our own short-lived launchctl diagnostic subprocess.

Never signals a service, process group or arbitrary PID. On failure only the
Popen child created here is reaped. This is not a service-stop implementation.
"""

import math
import os
import selectors
import subprocess
import time
from contextvars import ContextVar

LIMIT = 262144

# Internal helper scope only; ordinary diagnostic callers remain unchanged.
_capture_cancelled = ContextVar("mac_query_cancelled", default=None)


def _valid_timeout(timeout):
    return type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 10


def run_query(argv, *, stdin, stdout, stderr, timeout, check):
    """subprocess.run-shaped adapter with a fixed capture bound on POSIX.

    Total observation deadline includes pipe EOF and child exit. Cleanup has a
    separate bounded grace period; never communicate() after a capture failure.
    Intended only for launchctl print, which does not launch service processes.
    """
    if (
        len(argv) != 3
        or argv[:2] != ["/bin/launchctl", "print"]
        or stdin != subprocess.DEVNULL
        or stdout != subprocess.PIPE
        or stderr != subprocess.DEVNULL
        or check is not False
        or not _valid_timeout(timeout)
    ):
        raise ValueError("unsupported diagnostic command")
    return _capture(argv, timeout=timeout)


def run_kickstart(argv, *, stdin, stdout, stderr, timeout, check):
    """No -k, no signal: only start one already-loaded registered GUI target.

    Failure may have occurred after launchd accepted the request; never retry
    automatically. Cleanup only reaps launchctl, not the service it requested.
    """
    import re

    if (
        len(argv) != 3
        or argv[:2] != ["/bin/launchctl", "kickstart"]
        or not re.fullmatch(r"gui/[1-9][0-9]*/local\.ai-device\.client", argv[2])
        or stdin != subprocess.DEVNULL
        or stdout != subprocess.PIPE
        or stderr != subprocess.DEVNULL
        or check is not False
        or not _valid_timeout(timeout)
    ):
        raise ValueError("unsupported registered start command")
    return _capture(argv, timeout=timeout)


def run_bootstrap(argv, *, stdin, stdout, stderr, timeout, check):
    """Load one GUI agent definition; never `-k`, never a domain-wide sweep.

    Nonzero may mean already loaded, a refused definition or an unknown
    failure: callers must treat it as unknown and must not retry or unload.
    """
    import re

    if (
        len(argv) != 4
        or argv[:2] != ["/bin/launchctl", "bootstrap"]
        or not re.fullmatch(r"gui/[1-9][0-9]*", argv[2])
        or not re.fullmatch(r"/Users/[^/]+/Library/LaunchAgents/local\.ai-device\.client\.plist", argv[3])
        or stdin != subprocess.DEVNULL
        or stdout != subprocess.PIPE
        or stderr != subprocess.DEVNULL
        or check is not False
        or not _valid_timeout(timeout)
    ):
        raise ValueError("unsupported service load command")
    return _capture(argv, timeout=timeout)


def run_bootout(argv, *, stdin, stdout, stderr, timeout, check):
    """Unload one exact registered GUI target; no signals, no sweeps, no -k."""
    import re

    if (
        len(argv) != 3
        or argv[:2] != ["/bin/launchctl", "bootout"]
        or not re.fullmatch(r"gui/[1-9][0-9]*/local\.ai-device\.client", argv[2])
        or stdin != subprocess.DEVNULL
        or stdout != subprocess.PIPE
        or stderr != subprocess.DEVNULL
        or check is not False
        or not _valid_timeout(timeout)
    ):
        raise ValueError("unsupported service unload command")
    return _capture(argv, timeout=timeout)


def _capture(argv, *, timeout):
    # Private seam permits finite Python children in tests, not user commands.
    if not _valid_timeout(timeout):
        raise ValueError("invalid service observation timeout")
    cancelled = _capture_cancelled.get()

    def check_cancelled():
        if cancelled is not None and cancelled():
            raise ValueError("service observation cancelled")

    check_cancelled()
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    data = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                check_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired("launchctl diagnostic", timeout)
                events = selector.select(
                    min(remaining, 0.1) if cancelled is not None else remaining
                )
                if not events:
                    continue
                chunk = os.read(process.stdout.fileno(), min(65536, LIMIT + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > LIMIT:
                    raise ValueError("service observation exceeds capture limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("launchctl diagnostic", timeout)
            if cancelled is None:
                code = process.wait(timeout=remaining)
            else:
                while process.poll() is None:
                    check_cancelled()
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired("launchctl diagnostic", timeout)
                    time.sleep(0.01)
                check_cancelled()
                code = process.wait()
        return subprocess.CompletedProcess(argv, code, bytes(data))
    finally:
        process.stdout.close()
        if process.poll() is None:
            # Only this diagnostic child, never launchd's service PID.
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)
        else:
            process.wait()
