"""Cooperative instance ownership fence, not an OS security boundary."""

import fcntl
import os
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

MARKER = ".native-management.json"


@contextmanager
def management_lease(root, *, registration=False, recovery=False):
    root = Path(root).resolve()
    path = root / ".management.lock"
    fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        mode = fcntl.LOCK_EX if registration else fcntl.LOCK_SH
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "instance management registration or legacy maintenance is active"
            ) from exc
        marker = root / MARKER
        if not (registration and recovery) and (marker.exists() or marker.is_symlink()):
            raise ValueError("native-managed instance requires the native lifecycle manager")
        yield
    finally:
        os.close(fd)


def legacy_operation(function):
    @wraps(function)
    def wrapped(root, *args, **kwargs):
        with management_lease(root):
            return function(root, *args, **kwargs)

    return wrapped
