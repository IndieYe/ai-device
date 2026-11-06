"""Read-only registration ownership checks before lifecycle operations.

A matching on-disk definition is necessary, not proof of launchd's loaded
configuration or process state. Never infer stopped from this check alone.
"""

import os
import plistlib
import stat

from .client_macos_account import resolve_account
from .client_macos_identity import read_identity


class MacRegistrationError(ValueError):
    pass


def registered_account():
    identity = read_identity()
    account = resolve_account(identity["user"])
    if (
        account.name != identity["user"]
        or account.layout.uid != identity["uid"]
        or account.gid != identity["gid"]
        or str(account.layout.home) != identity["home"]
    ):
        raise MacRegistrationError("registered account changed; lifecycle operation refused")
    return account


def validate_agent(account):
    """Reject overrides, links and partial publication; do not load the job."""
    descriptor = None
    try:
        parent = account.layout.plist.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != account.layout.uid
            or parent.st_mode & 0o022
        ):
            raise MacRegistrationError("LaunchAgents directory is not owned by the registered user")
        descriptor = os.open(
            account.layout.plist, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != account.layout.uid
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise MacRegistrationError("registered agent permissions are invalid")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            data = stream.read(65537)
        if len(data) > 65536:
            raise MacRegistrationError("registered agent exceeds inspection limit")
        definition = plistlib.loads(data)
        if plistlib.dumps(definition, sort_keys=True) != account.layout.render():
            raise MacRegistrationError("registered agent has unsupported modifications")
        current = account.layout.plist.lstat()
        if (
            (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
            or not stat.S_ISREG(current.st_mode)
            or current.st_uid != account.layout.uid
            or current.st_mode & 0o077
            or current.st_nlink != 1
        ):
            raise MacRegistrationError("registered agent changed during inspection")
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException) as exc:
        if isinstance(exc, MacRegistrationError):
            raise
        raise MacRegistrationError("registered agent inspection failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
