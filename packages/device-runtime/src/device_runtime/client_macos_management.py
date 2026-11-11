"""Mac registration directory preparation and cooperative admin exclusion."""

import fcntl
import os
import stat
from contextlib import contextmanager
from pathlib import Path

MANAGEMENT_DIRECTORY = Path("/Library/Application Support/ai-device-client-management")


class MacManagementError(RuntimeError):
    pass


def ensure_directory(path, *, uid, gid, private=True):
    """Create only this directory; never repair existing user data recursively."""
    try:
        path.mkdir(mode=0o700 if private else 0o755)
    except FileExistsError:
        pass
    else:
        info = path.lstat()
        if (info.st_uid, info.st_gid) != (uid, gid):
            os.chown(path, uid, gid, follow_symlinks=False)
    info = path.lstat()
    forbidden = 0o077 if private else 0o022
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & forbidden:
        raise MacManagementError("directory ownership or permissions are incompatible")


def prepare_user_directories(account):
    """After account preflight, create only declared service directories."""
    layout = account.layout
    for path, private in (
        (layout.plist.parent, False),
        (layout.home / "Library/Logs", False),
        (layout.log.parent, True),
        (layout.state, True),
    ):
        ensure_directory(path, uid=layout.uid, gid=account.gid, private=private)


@contextmanager
def management_lock(directory=MANAGEMENT_DIRECTORY, *, owner_uid=0, owner_gid=0):
    """One machine-wide, nonblocking lock; never remove or replace its inode.

    Explicit alternate directories/owners support isolated tests. Production
    callers use the default root-owned location, independent of selected user.
    This does not acquire macOS Installer's own private serialization locks.
    """
    descriptor = None
    try:
        ensure_directory(directory, uid=owner_uid, gid=owner_gid)
        path = directory / "management.lock"
        descriptor = os.open(
            path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise MacManagementError("management lock file is invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MacManagementError("another client management operation is active") from None
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise MacManagementError("management lock identity changed")
    except BaseException as exc:
        if descriptor is not None:
            os.close(descriptor)
        if isinstance(exc, OSError):
            raise MacManagementError("management lock is unavailable") from None
        raise
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def management_lock_matches(descriptor, directory=MANAGEMENT_DIRECTORY, *, owner_uid=0):
    """Recheck a caller-owned locked FD; never acquire, unlock or repair it.

    Caller must continuously own the flock. File identity alone cannot prove
    flock ownership; this check is not an authorization or lock acquisition.
    """
    try:
        parent = directory.lstat()
        opened = os.fstat(descriptor)
        current = (directory / "management.lock").lstat()
        return (
            stat.S_ISDIR(parent.st_mode)
            and parent.st_uid == owner_uid
            and not parent.st_mode & 0o077
            and stat.S_ISREG(opened.st_mode)
            and stat.S_ISREG(current.st_mode)
            and opened.st_uid == current.st_uid == owner_uid
            and not (opened.st_mode | current.st_mode) & 0o077
            and opened.st_nlink == current.st_nlink == 1
            and (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)
        )
    except (OSError, TypeError, ValueError):
        return False
