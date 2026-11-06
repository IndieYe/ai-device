"""Fixed-fd service load relay; no argv/environment selection.

A trusted native parent holds management flock continuously and passes the same
open-file-description as fd4. This process NEVER unlocks it: LOCK_UN would also
release the parent's shared flock. Close only our own copy.

The module name itself is the whole command: the registered definition and the
gui domain cannot be chosen here. Success prints one fixed token; every failure
prints nothing and exits nonzero, which the parent treats as unknown.
"""

import argparse
import fcntl
import os
import signal
import sys

from .client_macos_management import MANAGEMENT_DIRECTORY, management_lock_matches
from .client_macos_service_job import MacServiceJobError, load_registered_service

MANAGEMENT_FD = 4
SUCCESS = "AI_DEVICE_REGISTERED_SERVICE_LOADED_V1"


def main(argv=None):
    descriptor = MANAGEMENT_FD
    cancelled = False

    def cancel(signum, frame):
        nonlocal cancelled
        cancelled = True

    previous = signal.signal(signal.SIGTERM, cancel)
    try:
        # Before parsing or any subprocess, prohibit further inheritance.
        os.set_inheritable(descriptor, False)
        parser = argparse.ArgumentParser(prog="managed-client-service-load")
        parser.add_argument("--inherit-management-lock", required=True, action="store_true")
        parser.parse_args(argv)
        if sys.platform != "darwin" or os.getuid() != 0 or os.geteuid() != 0:
            raise MacServiceJobError("administrator required")

        def current():
            return management_lock_matches(descriptor, MANAGEMENT_DIRECTORY, owner_uid=0)

        if not current():
            raise MacServiceJobError("management descriptor is unavailable")
        # Same inherited open-file-description: no second acquisition.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not current():
            raise MacServiceJobError("management descriptor changed")

        def guarded():
            return not cancelled and current()

        load_registered_service(guard_current=guarded)
        if cancelled:
            raise MacServiceJobError("service load cancelled")
        print(SUCCESS)
    except (MacServiceJobError, OSError, ValueError):
        raise SystemExit("registered service load unavailable; outcome may be unknown") from None
    finally:
        signal.signal(signal.SIGTERM, previous)
        try:
            os.close(descriptor)
        except OSError:
            pass


if __name__ == "__main__":
    main()
