"""Nonblocking cooperative run/replacement exclusion, not a stop proof.

The registrar must provision the private lock once. Runtime and installer use
this same path, never unlink it, and keep the descriptor for the whole protected
operation. Callers must still prove descendant quiescence separately.
"""

import fcntl
import os
import stat
from contextlib import contextmanager


class MacLockError(RuntimeError):
    pass


class MacLockBusy(MacLockError):
    pass


@contextmanager
def _private_lock(path, *, uid, shared=False):
    """Acquire an existing private lock without waiting or changing ownership.

    No creation here: opening a missing file must not silently invent a new
    lock identity during repair. Provisioning belongs to registration. CLOEXEC
    prevents unrelated command children from extending the lock lifetime.
    """
    descriptor = None
    try:
        parent = path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != uid or parent.st_mode & 0o077:
            raise MacLockError("runtime lock directory is not private")
        descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != uid
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise MacLockError("runtime lock file is invalid")
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MacLockBusy("client run or replacement is already active") from None
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise MacLockError("runtime lock identity changed")
    except BaseException as exc:
        if descriptor is not None:
            os.close(descriptor)
        if isinstance(exc, OSError):
            raise MacLockError("runtime lock is unavailable") from None
        raise
    try:
        yield
    finally:
        os.close(descriptor)


@contextmanager
def run_or_replace_lock(path, *, uid):
    """Exclusive runtime lifetime lock; preserve the existing public contract."""
    with _private_lock(path, uid=uid):
        yield


def provision_runtime_lock(path, *, uid, gid):
    """Provision once under registration's management lock; never replace.

    On creation failure leave the file for explicit repair, not unlink: another
    participant may already have opened its inode. Existing locks are validated
    even when held; provisioning is not permission to register or replace code.
    """
    if type(uid) is not int or uid <= 0 or type(gid) is not int or gid < 0:
        raise MacLockError("runtime lock requires an explicit ordinary account")
    descriptor = None
    created = False
    try:
        parent = path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != uid or parent.st_mode & 0o077:
            raise MacLockError("runtime lock directory is not private")
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            descriptor = os.open(path, flags)
        if created:
            info = os.fstat(descriptor)
            if (info.st_uid, info.st_gid) != (uid, gid):
                os.fchown(descriptor, uid, gid)
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != uid
            or info.st_gid != gid
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise MacLockError("runtime lock file is invalid")
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise MacLockError("runtime lock identity changed")
        return created
    except OSError:
        raise MacLockError("runtime lock provisioning failed; inspect registration") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def adopt_runtime_lock(path, *, uid, descriptor=3):
    """Consume an inherited lock descriptor from the experimental exec shim.

    Validate current ownership, not historical lock-before-interpreter timing.
    The native caller must establish that timing. Not wired to production entry.
    Ownership of a valid numeric descriptor transfers on entry, even on failure.
    """
    if type(descriptor) is not int or descriptor < 3:
        raise MacLockError("invalid inherited runtime descriptor")
    probe = None
    try:
        try:
            os.set_inheritable(descriptor, False)
            parent = path.parent.lstat()
            info = os.fstat(descriptor)
            current = path.lstat()
            if (
                not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != uid
                or parent.st_mode & 0o077
                or not stat.S_ISREG(info.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or info.st_uid != uid
                or info.st_mode & 0o077
                or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise MacLockError("inherited runtime lock identity is invalid")
            probe = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            other = os.fstat(probe)
            if (other.st_dev, other.st_ino) != (info.st_dev, info.st_ino):
                raise MacLockError("runtime lock identity changed")
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise MacLockError("inherited runtime lock was not held")
            # A conflict on probe alone could be another owner's lock. The
            # inherited open-file description must itself own exclusive flock.
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise MacLockError("inherited runtime lock is unavailable") from None
        finally:
            if probe is not None:
                os.close(probe)
        yield
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
