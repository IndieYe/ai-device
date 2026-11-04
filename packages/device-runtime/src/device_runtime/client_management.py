"""Serialize manual Linux client management with the system package transaction."""

import fcntl
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path

DPKG_LOCKS = (Path("/var/lib/dpkg/lock-frontend"), Path("/var/lib/dpkg/lock"))
MANAGEMENT_LOCK = Path("/run/ai-device-client-management.lock")


def in_package_script():
    # Coordination for trusted local entrypoints, not an authorization boundary.
    return os.environ.get("DPKG_MAINTSCRIPT_PACKAGE") == "ai-device-client" and os.environ.get(
        "DPKG_MAINTSCRIPT_NAME"
    ) in {"preinst", "prerm", "postinst"}


@contextmanager
def management_lock(*, package_script=False):
    with ExitStack() as stack:
        if not package_script:
            # dpkg uses POSIX record locks, not flock. Never unlink these files:
            # replacing a lock inode would break coordination with apt/dpkg.
            for path in DPKG_LOCKS:
                stream = stack.enter_context(path.open("r+"))
                fcntl.lockf(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stream = stack.enter_context(MANAGEMENT_LOCK.open("a"))
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
