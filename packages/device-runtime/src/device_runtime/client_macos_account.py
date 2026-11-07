"""Read-only preflight for an explicitly selected macOS client account.

No registration is performed here. Conflict discovery, management locking,
configuration parsing as the target user, and launchd checks remain mandatory
before a future registrar writes anything.
"""

import os
import pwd
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .client_macos_layout import LAUNCHER, PYTHON, MacClientLayout


class MacAccountError(ValueError):
    """Stable diagnostic that does not include configuration or credential data."""


@dataclass(frozen=True)
class MacClientAccount:
    name: str
    gid: int
    layout: MacClientLayout


def resolve_account(name, *, lookup=None):
    """Resolve exactly the requested name, never console user or environment."""
    if (
        not isinstance(name, str)
        or not name
        or name.startswith("-")
        or any(char.isspace() or ord(char) < 32 for char in name)
    ):
        raise MacAccountError("an explicit account name is required")
    try:
        account = (lookup or pwd.getpwnam)(name)
    except (KeyError, OSError):
        raise MacAccountError("selected account is unavailable") from None
    # Standard macOS interactive accounts have IDs >= 501. A system/service
    # account is not a supported GUI LaunchAgent target, even when non-root.
    if account.pw_uid < 501 or account.pw_shell in (
        "",
        "/usr/bin/false",
        "/bin/false",
        "/usr/sbin/nologin",
        "/sbin/nologin",
    ):
        raise MacAccountError("selected account is not a supported ordinary macOS user")
    if account.pw_name != name or account.pw_gid < 0:
        raise MacAccountError("selected account identity is inconsistent")
    try:
        layout = MacClientLayout(account.pw_uid, Path(account.pw_dir))
    except (ValueError, TypeError):
        raise MacAccountError("selected account home is invalid") from None
    return MacClientAccount(name, account.pw_gid, layout)


def _inspect(path):
    try:
        return path.lstat()
    except OSError:
        raise MacAccountError("required account path is unavailable") from None


def validate_launcher():
    """Refuse publishing a login entry for a missing or writable launcher.

    This checks the installed fixed path, not an arbitrary caller target. It
    does not prove Installer lifetime exclusion or replace build verification.
    """
    for path in reversed((LAUNCHER.parent, *LAUNCHER.parent.parents)):
        info = _inspect(path)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise MacAccountError("installed launcher directory is unavailable or writable")
    info = _inspect(LAUNCHER)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & 0o022
        or info.st_mode & 0o111 != 0o111
        or info.st_nlink != 1
    ):
        raise MacAccountError("installed native launcher is unavailable or invalid")


def validate_control_socket_path(account):
    """Reject paths Python cannot bind on Darwin, without touching the filesystem."""
    # Darwin sockaddr_un.sun_path[104]; Python requires room for the trailing NUL.
    path = os.fsencode(account.layout.state / "maintenance.sock")
    if b"\x00" in path or len(path) >= 104:
        raise MacAccountError("account home makes the maintenance socket path invalid or too long")


def validate_registration_files(account):
    """Validate existing files without creating, chowning or reading secrets.

    This is a preflight, not a TOCTOU/ACL guarantee. The eventual registrar
    must hold its management lock and validate actual target-user access.
    """
    validate_control_socket_path(account)
    layout = account.layout
    # Parent directories may be root-owned mounts; reject symlinks, files and
    # other-writable paths rather than following them into a different home.
    for path in reversed((layout.home, *layout.home.parents)):
        info = _inspect(path)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in (0, layout.uid)
            or info.st_mode & 0o022
        ):
            raise MacAccountError("account home path has incompatible ownership or permissions")
    if _inspect(layout.home).st_uid != layout.uid:
        raise MacAccountError("account home must belong to the selected user")
    for path in (layout.home / "Library", layout.directory.parent):
        info = _inspect(path)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != layout.uid or info.st_mode & 0o022:
            raise MacAccountError("account Library path has incompatible ownership or permissions")
    for path, directory in (
        (layout.directory, True),
        (layout.config, False),
        (layout.token, False),
    ):
        info = _inspect(path)
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(info.st_mode) or info.st_uid != layout.uid or info.st_mode & 0o077:
            raise MacAccountError(
                "client configuration must be private and owned by the selected user"
            )
    # Existing destination parents must also be safe. Missing directories
    # are left for the registrar to create under the validated user Library.
    for path in (
        layout.plist.parent,
        layout.home / "Library/Logs",
        layout.log.parent,
        layout.state,
    ):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise MacAccountError("registration destination is unavailable") from None
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != layout.uid or info.st_mode & 0o022:
            raise MacAccountError(
                "registration destination has incompatible ownership or permissions"
            )
    try:
        info = layout.log.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        raise MacAccountError("client log is unavailable") from None
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_uid != layout.uid or info.st_mode & 0o077:
            raise MacAccountError("client log must be private and owned by the selected user")
    # Registration is create-only. Existing links (including dangling ones)
    # are conflicts, not missing files to overwrite.
    try:
        layout.plist.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        raise MacAccountError("LaunchAgent registration state is unavailable") from None
    else:
        raise MacAccountError(
            "LaunchAgent already exists for this user; registration is create-only and "
            "will not overwrite it"
        )


# Fixed code, with paths in separate argv fields. No user data is interpolated
# into Python or shell source, and neither successful nor failed probes log it.
_CONFIG_PROBE = """
import json, os, sys
from pathlib import Path
from device_runtime.client_config import load, _read_regular
config, state, token, uid = sys.argv[1:]
if os.getuid() != int(uid):
    raise ValueError('unexpected account')
raw = json.loads(_read_regular(config, 65536))
if raw.get('state_dir') != state or raw.get('token_file') != token:
    raise ValueError('unexpected client paths')
settings, secret = load(config)
if settings.state_dir != Path(state):
    raise ValueError('unexpected state directory')
"""


def validate_config_as_user(account, *, run=None):
    """Run a bounded no-output config probe, never a login shell or client.

    Intended for an administrator registrar, after file/identity preflight.
    sudo -n must fail rather than asking for a password. Calling this function
    is an explicit operation; importing or rendering the layout never invokes it.
    """
    layout = account.layout
    argv = [
        "/usr/bin/sudo",
        "-n",
        "-H",
        "-u",
        f"#{layout.uid}",
        "--",
        str(PYTHON),
        "-I",
        "-B",
        "-c",
        _CONFIG_PROBE,
        str(layout.config),
        str(layout.state),
        str(layout.token),
        str(layout.uid),
    ]
    try:
        result = (run or subprocess.run)(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise MacAccountError("target-user configuration check failed") from None
    if result.returncode != 0:
        raise MacAccountError("target-user configuration check failed")
