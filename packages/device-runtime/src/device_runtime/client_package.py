"""Native package lifecycle for the configured single Linux client."""

import argparse
import re
import subprocess
import time
from pathlib import Path

from .client_conflicts import ClientConflict, check_conflicts
from .client_management import in_package_script, management_lock
from .client_service import SystemdClient
from .maintenance import ClientBusy, request
from .version import runtime_version

SOCKET = Path("/var/lib/ai-device-client/maintenance.sock")
CONFIG = Path("/etc/ai-device-client/client.json")
UNIT_PATH = Path("/etc/systemd/system/ai-device-client.service")


def activate(*, service=None, timeout=15):
    if not UNIT_PATH.exists() and not UNIT_PATH.is_symlink():
        return "installed; configuration and service registration required"
    if UNIT_PATH.is_symlink() or not UNIT_PATH.is_file():
        raise RuntimeError("client service ownership is unknown")
    if not CONFIG.is_file():
        raise RuntimeError("registered client configuration is missing")
    if (
        "ExecStart=/opt/ai-device-client/runtime/bin/python3 -I -m device_runtime.client_cli --config /etc/ai-device-client/client.json"
        not in UNIT_PATH.read_text().splitlines()
    ):
        raise RuntimeError("client service is not owned by this installation")
    service = service or SystemdClient()
    service.validate_config()
    check_conflicts()
    service.start()
    deadline = time.monotonic() + timeout
    expected = runtime_version()
    if not _known_identity(expected):
        # The running client could not fingerprint its own sources (OSError in
        # version.py). Reporting "unknown identity" as a match would let two
        # unidentifiable builds pass as the same ready target.
        raise RuntimeError(
            "client build identity is unknown; cannot verify readiness; inspect client.log"
        )
    while time.monotonic() < deadline:
        try:
            result = request(SOCKET, "status", "package_ready", timeout=1)
            # expected is already known to be fully specified, so an equal
            # response needs no separate build_id check.
            if result.get("ready") and result.get("runtime_version") == expected:
                return "installed; client locally ready"
        except (OSError, ValueError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError("client readiness/version verification failed; inspect client.log")


def _known_identity(value):
    """True only for a fully specified version and build identity.

    Missing and null both deserialize to build_id=None, and comparing two of
    those as equal would treat an uncomputable identity as a verified one.
    The header parser stays permissive on purpose: it is diagnostics, and an
    older device legitimately omits the field.
    """
    if not isinstance(value, dict):
        return False
    version, build_id = value.get("version"), value.get("build_id")
    return (
        isinstance(version, str)
        and bool(version)
        and isinstance(build_id, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", build_id) is not None
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stop", "activate"))
    # The package scripts pass this explicitly. Detecting the context by
    # environment does not work for rpm: measured on alinux3, an rpm scriptlet
    # sees no RPM_* variables at all, so an env-based check reported "manual"
    # and then tried to open dpkg locks that do not exist on that host.
    parser.add_argument(
        "--from-package-script",
        action="store_true",
        help="run inside a package manager scriptlet; the manager already holds its lock",
    )
    args = parser.parse_args(argv)
    in_script = args.from_package_script or in_package_script()
    try:
        with management_lock(package_script=in_script):
            if args.action == "stop":
                service = SystemdClient()
                if service.state() == "running":
                    service.validate_registration()
                    service.validate_config()
                    check_conflicts()
                service.stop_for_install(SOCKET)
                print("client confirmed stopped")
            else:
                print(activate())
    except ClientConflict as exc:
        parser.exit(1, str(exc) + "\n")
    except ClientBusy:
        parser.exit(
            1, "client is busy with active tasks; retry when tasks finish; service kept running\n"
        )
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
        # Package scripts must fail visibly; never expose token/config contents.
        parser.exit(
            1, "client lifecycle failed; inspect service state and client.log; no automatic retry\n"
        )


if __name__ == "__main__":
    main()
