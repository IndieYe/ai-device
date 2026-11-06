"""Single-machine registration identity; not an upgrade transaction journal."""

import json
import os
import stat
import tempfile
from pathlib import Path

from .client_macos_management import MANAGEMENT_DIRECTORY


class MacIdentityError(ValueError):
    pass


def reserve_identity(account, *, directory=MANAGEMENT_DIRECTORY):
    """Under management_lock, reserve one account before publishing its agent.

    No silent retry/adoption: even a matching existing record requires explicit
    repair, since a prior registration may have partially completed. Never
    delete a reservation on later failure or change the device's configuration.
    """
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
        raise MacIdentityError("registration identity directory is not private")
    payload = {
        "schema_version": 1,
        "user": account.name,
        "uid": account.layout.uid,
        "gid": account.gid,
        "home": str(account.layout.home),
    }
    fd, name = tempfile.mkstemp(prefix=".identity-", suffix=".tmp", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, directory / "registration.json", follow_symlinks=False)
        except FileExistsError:
            raise MacIdentityError(
                "client identity already reserved; explicit repair required"
            ) from None
    finally:
        temporary.unlink()


def read_identity(*, directory=MANAGEMENT_DIRECTORY):
    """Read a bounded identity for later explicit repair, never infer a user."""
    fd = None
    try:
        parent = directory.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or parent.st_mode & 0o077
        ):
            raise MacIdentityError("registration identity directory is not private")
        fd = os.open(
            directory / "registration.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        )
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise MacIdentityError("registration identity permissions are invalid")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            data = stream.read(8193)
        if len(data) > 8192:
            raise MacIdentityError("registration identity is too large")
        value = json.loads(data)
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "user",
            "uid",
            "gid",
            "home",
        }:
            raise MacIdentityError("registration identity is invalid")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise MacIdentityError("registration identity version is unsupported")
        if (
            type(value["uid"]) is not int
            or value["uid"] <= 0
            or type(value["gid"]) is not int
            or value["gid"] < 0
        ):
            raise MacIdentityError("registration identity account is invalid")
        if (
            not isinstance(value["user"], str)
            or not value["user"]
            or not isinstance(value["home"], str)
            or not Path(value["home"]).is_absolute()
        ):
            raise MacIdentityError("registration identity account is invalid")
        from .client_macos_layout import MacClientLayout

        if value["user"].startswith("-") or any(
            char.isspace() or ord(char) < 32 for char in value["user"]
        ):
            raise MacIdentityError("registration identity account is invalid")
        MacClientLayout(value["uid"], Path(value["home"]))
        current = (directory / "registration.json").lstat()
        if (
            (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
            or not stat.S_ISREG(current.st_mode)
            or current.st_uid != os.geteuid()
            or current.st_mode & 0o077
            or current.st_nlink != 1
        ):
            raise MacIdentityError("registration identity changed during inspection")
        return value
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, MacIdentityError):
            raise
        raise MacIdentityError(
            "registration identity unavailable; explicit repair required"
        ) from None
    finally:
        if fd is not None:
            os.close(fd)
