"""Optional system details sent in a backwards-compatible connection header."""

import json
import platform
import re
import sys
from pathlib import Path

from pydantic import Field, ValidationError

from .protocol import Model

SYSTEM_INFO_HEADER = "X-Device-System-Info"

# Written by the native package builders next to the runtime tree they ship.
# Reading a file rather than querying dpkg/rpm/pkgutil keeps the connection path
# free of subprocesses and works for the installed layout on every platform.
PACKAGE_VERSION_FILE = "client-package-version.json"
PACKAGE_VERSION_MAX_BYTES = 4096
PACKAGE_VERSION_PATTERN = re.compile(r"[A-Za-z0-9.+~_-]{1,80}")


class SystemInfo(Model):
    version: str | None = Field(default=None, min_length=1, max_length=160)
    architecture: str | None = Field(default=None, min_length=1, max_length=80)
    # Native package version (deb/rpm/pkg), which is independent of the Python
    # runtime version: the same 0.5.x installer can ship different runtimes.
    # Absent for source installs and for clients that predate this field.
    package_version: str | None = Field(
        default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9.+~_-]+$"
    )


def _package_version_file():
    # Same tree-root walk as version.py: the file sits beside runtime/, so it
    # moves with the payload instead of depending on the build machine.
    root = Path(__file__).parent
    return root.parents[4] / PACKAGE_VERSION_FILE if len(root.parents) > 4 else None


def collect_package_version():
    # Diagnostic only: a missing, unreadable or malformed file reports nothing
    # rather than failing the connection or guessing a version.
    path = _package_version_file()
    if path is None:
        return None
    try:
        if not path.is_file() or path.stat().st_size > PACKAGE_VERSION_MAX_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("package_version")
    if not isinstance(version, str) or not PACKAGE_VERSION_PATTERN.fullmatch(version):
        return None
    return version


def collect_system_info():
    # Device information is diagnostic only; inaccessible OS metadata must not
    # prevent a connection. Platform names are provided by the existing hello.
    try:
        if sys.platform == "darwin":
            version = platform.mac_ver()[0] or None
        elif sys.platform == "linux":
            release = platform.freedesktop_os_release()
            version = release.get("PRETTY_NAME") or release.get("VERSION_ID") or None
        else:
            version = None
    except (OSError, ValueError):
        version = None
    try:
        architecture = platform.machine() or None
    except OSError:
        architecture = None
    # Drop individual unusable values instead of failing the connection.
    version = version if isinstance(version, str) and 0 < len(version) <= 160 else None
    architecture = architecture if isinstance(architecture, str) and 0 < len(architecture) <= 80 else None
    return SystemInfo(
        version=version,
        architecture=architecture,
        package_version=collect_package_version(),
    )


def system_info_header(info):
    return json.dumps(info.model_dump(), ensure_ascii=True, separators=(",", ":"))


def parse_system_info_header(value):
    if value is None or len(value) > 2048:
        return None
    try:
        return SystemInfo.model_validate_json(value)
    except ValidationError:
        return None
