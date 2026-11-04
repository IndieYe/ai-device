"""Register one explicitly configured Linux client; never start it implicitly."""

import argparse
import os
import pwd
import stat
import subprocess
import sys
from pathlib import Path

from .client_conflicts import ClientConflict, check_conflicts
from .client_management import management_lock
from .client_service import UNIT, render_unit

CONFIG = Path("/etc/ai-device-client")
STATE = Path("/var/lib/ai-device-client")
LOGS = Path("/var/log/ai-device-client")
UNIT_PATH = Path("/etc/systemd/system") / UNIT
PYTHON = "/opt/ai-device-client/runtime/bin/python3"


def private_directory(path, uid, gid):
    """Prepare only the declared directory, never recursively chown user data."""
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
            raise ValueError("existing directory has incompatible ownership or permissions")
    else:
        os.chown(path, uid, gid)


def register(user, *, allow_root=False, confirm_legacy_clients_disabled=False):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("Linux service registration requires administrator privileges")
    if not confirm_legacy_clients_disabled:
        raise ClientConflict(
            "confirm custom legacy clients are stopped and auto-start disabled with --confirm-legacy-clients-disabled"
        )
    unit = render_unit(user)
    account = pwd.getpwnam(user)
    if account.pw_uid == 0 and not allow_root:
        raise ValueError("root service requires explicit --allow-root")
    # Coordinate manual registration with apt/dpkg and other management calls.
    with management_lock():
        check_conflicts()
        if UNIT_PATH.exists() or UNIT_PATH.is_symlink():
            raise ValueError("client service already exists; registration will not overwrite it")
        for path in (CONFIG, CONFIG / "client.json", CONFIG / "token"):
            info = path.lstat()
            expected = stat.S_ISDIR if path == CONFIG else stat.S_ISREG
            if not expected(info.st_mode) or info.st_uid != account.pw_uid or info.st_mode & 0o077:
                raise ValueError("configuration must be private and owned by the service user")
        # Validate using the actual account and interpreter, not root's ability
        # to read credentials. No token or parsed config is printed.
        probe = subprocess.run(
            [
                "runuser",
                "-u",
                user,
                "--",
                PYTHON,
                "-I",
                "-B",
                "-c",
                (
                    "from device_runtime.client_config import load; "
                    "a,t=load('/etc/ai-device-client/client.json'); "
                    "assert str(a.state_dir)=='/var/lib/ai-device-client'"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if probe.returncode:
            raise ValueError(
                "configuration is not usable by the service user or state path differs"
            )
        private_directory(STATE, account.pw_uid, account.pw_gid)
        private_directory(LOGS, account.pw_uid, account.pw_gid)
        # Exclusive creation avoids clobbering an unrelated concurrent unit.
        descriptor = os.open(UNIT_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(unit)
            stream.flush()
            os.fsync(stream.fileno())
        # On failure retain the unit and data for explicit diagnosis; do not
        # remove files after systemd may already have consumed them.
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=30)
        subprocess.run(["systemctl", "enable", UNIT], check=True, timeout=30)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--allow-root", action="store_true")
    parser.add_argument(
        "--confirm-legacy-clients-disabled",
        action="store_true",
        help="Confirm custom legacy clients are stopped and their auto-start disabled; never bypasses detected conflicts",
    )
    args = parser.parse_args(argv)
    try:
        register(
            args.user,
            allow_root=args.allow_root,
            confirm_legacy_clients_disabled=args.confirm_legacy_clients_disabled,
        )
    except ClientConflict as exc:
        parser.exit(2, str(exc) + "\n")
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        parser.exit(
            2, "registration failed; check user, private configuration and existing service\n"
        )
    print("Registered and enabled; service has not been started.")


if __name__ == "__main__":
    main()
