"""Conservative process evidence for Mac installer development.

Absence in a snapshot is NOT proof of quiescence: unseen/reparented children
and concurrent restarts require a separate lifetime/start barrier. This module
never signals processes or authorizes package replacement.
"""

import math
from dataclasses import dataclass


class ProcessEvidenceError(ValueError):
    """Process inspection is incomplete or inconsistent; replacement must wait."""


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    started: float

    def __post_init__(self):
        if type(self.pid) is not int or self.pid <= 0:
            raise ProcessEvidenceError("invalid process identity")
        if (
            type(self.started) not in (int, float)
            or not math.isfinite(self.started)
            or self.started <= 0
        ):
            raise ProcessEvidenceError("invalid process start time")


@dataclass(frozen=True)
class ProcessObservation:
    identity: ProcessIdentity
    parent_pid: int

    def __post_init__(self):
        if not isinstance(self.identity, ProcessIdentity):
            raise ProcessEvidenceError("invalid process observation")
        if (
            type(self.parent_pid) is not int
            or self.parent_pid < 0
            or self.parent_pid == self.identity.pid
        ):
            raise ProcessEvidenceError("invalid parent process")


class ObservedProcessTree:
    """Remember positive evidence across snapshots, including reparenting.

    Input must be a complete snapshot from a future platform adapter. Failure
    to inspect a process must not be represented as an empty snapshot. PID
    reuse is distinguished by creation time, not by a stored PID alone.
    """

    def __init__(self, root):
        if not isinstance(root, ProcessIdentity):
            raise ProcessEvidenceError("invalid root process")
        self.observed = {root}

    def observe(self, processes, *, complete):
        if complete is not True:
            raise ProcessEvidenceError("process snapshot is incomplete")
        by_pid = {}
        for process in processes:
            if not isinstance(process, ProcessObservation):
                raise ProcessEvidenceError("invalid process observation")
            if process.identity.pid in by_pid:
                raise ProcessEvidenceError("duplicate PID in process snapshot")
            by_pid[process.identity.pid] = process
        # Match a parent by its identity in this snapshot; a reused PID is not
        # the former parent. Previously observed children remain tracked even
        # when their current parent is no longer the original client.
        active = {p.identity for p in by_pid.values() if p.identity in self.observed}
        while True:
            children = {
                p.identity
                for p in by_pid.values()
                if p.parent_pid in by_pid and by_pid[p.parent_pid].identity in active
            }
            expanded = active | children
            if expanded == active:
                break
            active = expanded
        self.observed.update(active)
        return frozenset(active)


def inspect_identity(pid):
    """Read a single identity without signalling; unknown never means absent.

    Zombies remain present here: complete process disappearance is required.
    This does not authenticate a maintenance socket's peer or its ownership.
    """
    import psutil

    if type(pid) is not int or pid <= 0:
        raise ProcessEvidenceError("explicit positive process PID required")
    try:
        process = psutil.Process(pid)
        started = process.create_time()
        # A second observation detects reuse during this inspection where
        # visible; no claim of an atomic process handle is made.
        if psutil.Process(pid).create_time() != started:
            raise ProcessEvidenceError("process changed during inspection")
        return ProcessIdentity(pid, started)
    except psutil.NoSuchProcess:
        return None
    except (psutil.Error, OSError):
        raise ProcessEvidenceError("process identity unavailable") from None


def wait_original_exit(
    identity, *, timeout=15, interval=0.05, inspect=None, clock=None, sleep=None
):
    """Wait for this identity only; never reports replacement safety.

    PID reuse means the original identity is gone, not that its successor is
    stopped. The deadline bounds polling/waits, not a blocking OS inspection.
    A caller must bind the identity to its lease peer and establish a separate
    startup barrier before any mutation. No signals or reconnects are sent.
    """
    import time

    if not isinstance(identity, ProcessIdentity):
        raise ProcessEvidenceError("explicit original process identity required")
    if any(
        type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (timeout, interval)
    ):
        raise ProcessEvidenceError("positive finite observation timing required")
    inspect = inspect or inspect_identity
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    deadline = clock() + timeout
    while True:
        current = inspect(identity.pid)
        if clock() >= deadline:
            raise ProcessEvidenceError("original process exit was not confirmed before deadline")
        if current is not None and not isinstance(current, ProcessIdentity):
            raise ProcessEvidenceError("invalid process observation")
        if current is not None and current.pid != identity.pid:
            raise ProcessEvidenceError("process inspection returned a different PID")
        if current is None or current != identity:
            return
        sleep(min(interval, max(0, deadline - clock())))
