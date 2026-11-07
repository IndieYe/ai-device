"""Known Mac instance/service discovery. Not yet a complete registration gate.

Running-process inspection and explicit custom-instance confirmation remain
required by the registrar. No deletion, migration or service calls occur here.
"""

import json
import os
import plistlib
import re
import stat
import sys

FIXED_LAUNCHER = "/opt/ai-device-client/bin/ai-device-client-launcher"
LABEL = "local.ai-device.client"


class MacClientConflict(ValueError):
    pass


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise MacClientConflict("service or instance definition is not a regular file")
        data = stream.read(262145)
        if len(data) > 262144:
            raise MacClientConflict("service or instance definition exceeds inspection limit")
        return data


def _entries(path):
    try:
        entries = []
        with os.scandir(path) as scan:
            for item in scan:
                if len(entries) >= 4096:
                    raise MacClientConflict("client discovery directory exceeds inspection limit")
                entries.append(item.name)
        return entries
    except FileNotFoundError:
        # A dangling directory link is an inspection failure, not absence.
        if path.is_symlink():
            raise MacClientConflict("client discovery directory is unavailable") from None
        return []


def check_known_files(homes, *, service_directories, own_plist=None):
    """Inspect explicit account homes and system service directories.

    Include all account homes and /Library/{LaunchAgents,LaunchDaemons} in the
    eventual host adapter. No arbitrary filesystem scan or own-service bypass.
    """
    homes = tuple(homes)
    try:
        for home in homes:
            instances = home / ".device-runtime/instances"
            for name in _entries(instances):
                config = instances / name / "config/roles.json"
                try:
                    data = _read(config)
                except FileNotFoundError:
                    if config.is_symlink():
                        raise MacClientConflict("instance definition is unavailable") from None
                    continue
                roles = json.loads(data)
                if not isinstance(roles, dict):
                    raise MacClientConflict("instance definition is inconclusive")
                if "client" in roles:
                    raise MacClientConflict(
                        "legacy client instance detected; explicit retirement required"
                    )
        directories = list(service_directories) + [home / "Library/LaunchAgents" for home in homes]
        for directory in directories:
            for name in _entries(directory):
                if not name.endswith(".plist"):
                    continue
                definition = plistlib.loads(_read(directory / name))
                if not isinstance(definition, dict):
                    raise MacClientConflict("service definition is inconclusive")
                label = definition.get("Label", "")
                argv = definition.get("ProgramArguments", [])
                program = definition.get("Program", "")
                # Our own published definition is not a conflicting third-party
                # service. Reporting it as one sends the operator looking for a
                # second client that does not exist. It is still refused, just
                # for the actual reason: registration is create-only.
                if (
                    not isinstance(label, str)
                    or not isinstance(program, str)
                    or not isinstance(argv, list)
                    or any(not isinstance(x, str) for x in argv)
                ):
                    raise MacClientConflict("service definition is inconclusive")
                if own_plist is not None and (directory / name) == own_plist and label == LABEL:
                    raise MacClientConflict(
                        "already registered for this user; registration is create-only, "
                        "so use the existing definition or retire it first"
                    )
                command = " ".join([program, *argv])
                if (
                    label.startswith("com.device-runtime.")
                    or label == "local.ai-device.client"
                    or program == FIXED_LAUNCHER
                    or is_client_command(argv)
                    or re.search(
                        r"device_runtime\.(?:client_cli|client_macos_entry|cli\s+client)\b|device-runtime\s+client\b|/run\s+client\b|/launchd/run-client\.sh\b",
                        command,
                    )
                ):
                    raise MacClientConflict(
                        "another client service detected; explicit retirement required"
                    )
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException) as exc:
        if isinstance(exc, MacClientConflict):
            raise
        raise MacClientConflict("client discovery failed; registration refused") from None


def is_client_command(argv):
    """Match execution positions, not arbitrary substrings in unrelated data."""
    if not argv:
        return False
    from pathlib import Path

    if argv[0] == FIXED_LAUNCHER:
        return True
    for index, value in enumerate(argv[:-1]):
        if value == "-m":
            if argv[index + 1] in (
                "device_runtime.client_cli",
                "device_runtime.client_macos_entry",
            ):
                return True
            if argv[index + 1 : index + 3] == ["device_runtime.cli", "client"]:
                return True
    if Path(argv[0]).name in ("device-runtime", "device_runtime") and "client" in argv[1:]:
        return True
    return any(
        (Path(value).name == "run" and argv[index + 1 : index + 2] == ["client"])
        or value.endswith("/launchd/run-client.sh")
        for index, value in enumerate(argv)
    )


def check_registration_conflicts(
    *, confirm_legacy_clients_disabled=False, accounts=None, processes=None, own_plist=None
):
    """Host registration preflight; no own-process exception for first install.

    Must run under administrator management exclusion. psutil is provided by
    the client dependency set. Permission failures are not treated as absence.
    """
    import pwd
    from pathlib import Path

    import psutil

    if confirm_legacy_clients_disabled is not True:
        raise MacClientConflict("confirm custom legacy clients are stopped and auto-start disabled")
    try:
        records = (accounts or pwd.getpwall)()
        homes = set()
        for record in records:
            if not isinstance(record.pw_dir, str):
                raise MacClientConflict("account home discovery is inconclusive")
            if record.pw_dir.startswith("/"):
                homes.add(Path(record.pw_dir))
        check_known_files(
            homes,
            service_directories=[Path("/Library/LaunchAgents"), Path("/Library/LaunchDaemons")],
            own_plist=own_plist,
        )
        for process in (processes or psutil.process_iter)():
            try:
                argv = process.cmdline()
            except psutil.NoSuchProcess:
                continue
            if not isinstance(argv, list) or any(not isinstance(arg, str) for arg in argv):
                raise MacClientConflict("process command inspection is inconclusive")
            if is_client_command(argv):
                raise MacClientConflict(
                    "running client detected; stop it explicitly before registration"
                )
    except (OSError, psutil.Error):
        raise MacClientConflict("host client discovery failed; registration refused") from None


def main(argv=None):
    """Internal read-only probe for the future fixed-resource native adapter.

    Only the exact success token with exit status zero means clear. The caller
    must retain management exclusion and bound execution time; this entry does
    not acquire locks or grant publication permission by itself.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Read-only macOS client conflict check")
    parser.add_argument("--confirm-legacy-clients-disabled", action="store_true", required=True)
    parser.parse_args(argv)
    if sys.platform != "darwin" or os.geteuid() != 0:
        print("Mac host conflict check refused", file=sys.stderr)
        return 70
    try:
        check_registration_conflicts(confirm_legacy_clients_disabled=True)
    except Exception:  # noqa: BLE001 - process boundary: fixed failure, never a traceback
        # A diagnostic child must never turn an unexpected discovery error into
        # success, or expose account paths/process arguments through a traceback.
        print("Mac host conflict check refused", file=sys.stderr)
        return 70
    print("AI_DEVICE_HOST_CONFLICTS_CLEAR_V1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
