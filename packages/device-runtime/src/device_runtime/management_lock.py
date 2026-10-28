"""Cooperative instance ownership fence, not an OS security boundary."""

import fcntl
import os
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

MARKER = ".native-management.json"


@contextmanager
def management_lease(root, *, registration=False, recovery=False, deregistration=False):
    root = Path(root).resolve()
    path = root / ".management.lock"
    fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        # Deregistration mutates ownership state, so it needs the exclusive lock.
        mode = fcntl.LOCK_EX if (registration or deregistration) else fcntl.LOCK_SH
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "instance management registration or legacy maintenance is active"
            ) from exc
        marker = root / MARKER
        # Registration may only pass on a first install or an approved recovery.
        # Deregistration is the one operation that must run *with* the marker
        # present, because that marker is what proves the instance is native.
        if deregistration:
            if not marker.is_file() or marker.is_symlink():
                raise ValueError("deregistration requires a registered native instance")
        elif not (registration and recovery) and (marker.exists() or marker.is_symlink()):
            raise ValueError("native-managed instance requires the native lifecycle manager")
        yield
    finally:
        os.close(fd)


@contextmanager
def native_preparation_lease(root):
    """Exclusive native maintenance lease; caller validates schema2 binding.

    Does not grant registration recovery or permit legacy writes. The named
    native entry must authenticate the owner and marker before using this.
    """
    root = Path(root).resolve()
    fd = os.open(root / ".management.lock", os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("native preparation or instance maintenance active") from exc
        if not (root / MARKER).is_file() or (root / MARKER).is_symlink():
            raise ValueError("native preparation requires a registered instance")
        yield
    finally:
        os.close(fd)


def legacy_operation(function):
    @wraps(function)
    def wrapped(root, *args, **kwargs):
        with management_lease(root):
            return function(root, *args, **kwargs)

    return wrapped
