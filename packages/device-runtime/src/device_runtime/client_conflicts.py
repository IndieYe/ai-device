"""Conservative discovery of known Linux client conflicts, never migration.

Arbitrary offline custom roots cannot be enumerated. Registration separately
requires administrator confirmation that those clients cannot auto-start.
"""

import json
import os
import pwd
import re
import stat
import subprocess
from pathlib import Path

PROC = Path("/proc")
UNIT_DIRS = (
    Path("/etc/systemd/system"),
    Path("/usr/lib/systemd/system"),
    Path("/run/systemd/system"),
    Path("/etc/systemd/user"),
    Path("/usr/lib/systemd/user"),
    Path("/run/systemd/user"),
)
OWN_UNIT = Path("/etc/systemd/system/ai-device-client.service")
OWN_PYTHON = "/opt/ai-device-client/runtime/bin/python3"
OWN_CONFIG = "/etc/ai-device-client/client.json"
LEGACY_DIRS = (Path("/opt/ai-device/packages"), Path("/opt/ai-device/bootstrap"))


class ClientConflict(RuntimeError):
    """A detected conflict, or discovery could not complete safely."""


def _read(path, limit=65536):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ClientConflict("client conflict inspection requires regular files")
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ClientConflict("client conflict inspection limit exceeded")
    return value


def _client_command(argv):
    for i, arg in enumerate(argv):
        if arg == "-m" and i + 1 < len(argv):
            if argv[i + 1] == "device_runtime.client_cli":
                return True
            if argv[i + 1 : i + 3] == ["device_runtime.cli", "client"]:
                return True
    return bool(
        argv
        and Path(argv[0]).name in {"device-runtime", "ai-device-client"}
        and (Path(argv[0]).name == "ai-device-client" or "client" in argv[1:])
    )


def _own_command(argv):
    return argv == [OWN_PYTHON, "-I", "-m", "device_runtime.client_cli", "--config", OWN_CONFIG]


def _unit_client(text):
    return bool(
        re.search(
            r"device_runtime\.(?:client_cli|cli\s+client)\b|device-runtime\s+client\b|ai-device-client\b|/run\s+client\b",
            text,
        )
    )


def _service_pid():
    result = subprocess.run(
        ["systemctl", "show", "ai-device-client.service", "--property=MainPID", "--value"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode or not result.stdout.strip().isdigit():
        raise ClientConflict("client service identity inspection failed")
    return int(result.stdout.strip())


def check_conflicts():
    """Root-only call before registration and activation; errors expose no data."""
    try:
        if any(path.exists() or path.is_symlink() for path in LEGACY_DIRS):
            raise ClientConflict(
                "legacy client layout detected; disable/remove its registration explicitly"
            )
        homes = {
            Path(account.pw_dir) for account in pwd.getpwall() if account.pw_dir.startswith("/")
        }
        for home in homes:
            instances = home / ".device-runtime/instances"
            if not instances.exists():
                continue
            for instance in instances.iterdir():
                config = instance / "config/roles.json"
                if config.exists():
                    roles = json.loads(_read(config))
                    if not isinstance(roles, dict):
                        raise ClientConflict("legacy instance inspection is inconclusive")
                    if "client" in roles:
                        raise ClientConflict(
                            "legacy client instance detected; explicit retirement required"
                        )
        directories = list(UNIT_DIRS) + [home / ".config/systemd/user" for home in homes]
        for directory in directories:
            if not directory.exists():
                continue
            for unit in directory.glob("*.service"):
                if unit == OWN_UNIT or unit.resolve() == Path("/dev/null"):
                    continue  # own registration is separately validated; masked units cannot start
                if _unit_client(_read(unit).decode("utf-8")):
                    raise ClientConflict(
                        "another client service detected; disable its registration explicitly"
                    )
        service_pid = _service_pid()
        for process in PROC.iterdir():
            if not process.name.isdigit() or int(process.name) == os.getpid():
                continue
            try:
                argv = _read(process / "cmdline").decode("utf-8").rstrip("\0").split("\0")
            except (FileNotFoundError, ProcessLookupError):
                continue  # process exited during inspection
            if _client_command(argv) and not (
                int(process.name) == service_pid and _own_command(argv)
            ):
                raise ClientConflict(
                    "another running client detected; stop it explicitly before activation"
                )
    except ClientConflict:
        raise
    except (OSError, ValueError, UnicodeError, TypeError, subprocess.SubprocessError):
        raise ClientConflict(
            "client conflict inspection failed; resolve it before registration/activation"
        ) from None
