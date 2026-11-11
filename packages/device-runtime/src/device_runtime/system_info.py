"""Optional system details sent in a backwards-compatible connection header."""

import json
import platform
import sys

from pydantic import Field, ValidationError

from .protocol import Model

SYSTEM_INFO_HEADER = "X-Device-System-Info"


class SystemInfo(Model):
    version: str | None = Field(default=None, min_length=1, max_length=160)
    architecture: str | None = Field(default=None, min_length=1, max_length=80)


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
    return SystemInfo(version=version, architecture=architecture)


def system_info_header(info):
    return json.dumps(info.model_dump(), ensure_ascii=True, separators=(",", ":"))


def parse_system_info_header(value):
    if value is None or len(value) > 2048:
        return None
    try:
        return SystemInfo.model_validate_json(value)
    except ValidationError:
        return None
