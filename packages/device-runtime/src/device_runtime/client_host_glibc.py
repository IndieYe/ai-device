"""Refuse to run the packaged client on a host whose glibc is too old.

The tree ships a private CPython build plus wheels. The package metadata tells
the package manager the floor, but the new client path never consults any
package manager: it is started directly by systemd from an absolute path. A
host below the floor therefore installs and registers fine, then dies inside
the dynamic loader with "GLIBC_2.17 not found" at first start, which is hard to
read and arrives far from the cause.

Checking here keeps the failure where the operator can act on it and applies to
every start, not only the packaged install.
"""

from __future__ import annotations

import platform
import re

# Needs to stay equal to the packaged tree's real requirement; see
# device_runtime.dependencies.MINIMUM_GLIBC and the package builders.
MINIMUM_GLIBC = "2.17"


class UnsupportedHostError(RuntimeError):
    """The host cannot run the packaged interpreter; the message is user-facing."""


def _version_tuple(text):
    parts = re.match(r"(\d+)\.(\d+)", text or "")
    if not parts:
        return None
    return int(parts.group(1)), int(parts.group(2))


def host_glibc():
    """Return the host glibc version as (major, minor), or None if unknown."""
    libc, version = platform.libc_ver()
    if libc == "glibc":
        return _version_tuple(version)
    # platform.libc_ver() shells out to ldd; fall back to the direct answer the
    # C library itself provides when that probe fails.
    try:
        import os

        return _version_tuple(os.confstr("CS_GNU_LIBC_VERSION").split()[-1])
    except (AttributeError, OSError, ValueError):
        return None


def check_host(saved_minimum=None):
    """Raise UnsupportedHostError unless the host glibc meets the floor."""
    floor = _version_tuple(saved_minimum or MINIMUM_GLIBC)
    if floor is None:
        return
    actual = host_glibc()
    if actual is None:
        # Unknown is not proof of incompatibility: musl and unusual hosts
        # simply have no glibc answer to compare. Startup stays available;
        # the loader will produce the authoritative error if one is needed.
        return
    if actual < floor:
        raise UnsupportedHostError(
            "this client requires glibc {}.{} or newer; the host reports {}.{} "
            "({}/{}); install on a supported distribution".format(
                floor[0],
                floor[1],
                actual[0],
                actual[1],
                platform.system() or "unknown",
                platform.machine() or "unknown",
            )
        )
