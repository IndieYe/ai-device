"""Serialize manual Linux client management with the system package transaction.

The package manager differs per distribution and so does the lock that guards
its transaction. dpkg has two frontend locks and sets DPKG_MAINTSCRIPT_* while a
maintainer script runs; rpm holds its own transaction lock and marks scriptlets
through RPM_INSTALL_PREFIX-style variables instead. Both are supported here so
the client does not silently lose mutual exclusion on one of them: on alinux3
the previous dpkg-only version raised FileNotFoundError on
/var/lib/dpkg/lock-frontend before doing anything at all.
"""

import fcntl
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path

# dpkg uses POSIX record locks on these, not flock.
DPKG_LOCKS = (Path("/var/lib/dpkg/lock-frontend"), Path("/var/lib/dpkg/lock"))
MANAGEMENT_LOCK = Path("/run/ai-device-client-management.lock")


def package_manager():
    """Which transaction lock applies here, or None if neither is present.

    Presence of the lock file is the signal: dpkg creates its locks on first
    use, rpm ships /var/lib/rpm/.rpm.lock with the database. A host with
    neither is not a package-managed client host, and requiring a lock there
    would block a legitimate manual run.
    """
    if any(path.exists() for path in DPKG_LOCKS):
        return "dpkg"
    if Path("/var/lib/rpm").is_dir():
        return "rpm"
    return None


def in_package_script():
    # Coordination for trusted local entrypoints, not an authorization boundary.
    #
    # Package scripts should pass --from-package-script; this is the fallback for
    # callers that cannot. dpkg exports DPKG_MAINTSCRIPT_*, but rpm exports
    # nothing: measured on alinux3, an rpm scriptlet's environment contained no
    # RPM_* variable at all. There the only reliable signal is the parent
    # process, which is rpm itself.
    if os.environ.get("DPKG_MAINTSCRIPT_PACKAGE") == "ai-device-client" and os.environ.get(
        "DPKG_MAINTSCRIPT_NAME"
    ) in {"preinst", "prerm", "postinst"}:
        return True
    try:
        parent = Path(f"/proc/{os.getppid()}/comm").read_text().strip()
    except OSError:
        return False
    return parent == "rpm"


@contextmanager
def management_lock(*, package_script=False):
    with ExitStack() as stack:
        if not package_script and package_manager() == "dpkg":
            # Never unlink these files: replacing a lock inode would break
            # coordination with apt/dpkg.
            for path in DPKG_LOCKS:
                if not path.exists():
                    continue
                stream = stack.enter_context(path.open("r+"))
                fcntl.lockf(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # An rpm transaction already holds its own database lock, and a scriptlet
        # is serialized by rpm itself, so there is nothing to take there. The
        # management lock below still applies.
        stream = stack.enter_context(MANAGEMENT_LOCK.open("a"))
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
