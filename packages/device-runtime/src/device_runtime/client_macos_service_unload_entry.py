"""Fixed-fd service unload relay; no argv/environment selection.

Mirror of the load entry: the native parent holds management flock continuously
and passes the same open-file-description as fd4, which this process only closes.
The registered target cannot be selected here. Success prints one fixed token;
a failure means the outcome is unknown, and the parent must not replay it.
"""

import argparse
import fcntl
import os
import signal
import sys

from .client_macos_management import MANAGEMENT_DIRECTORY, management_lock_matches
from .client_macos_service_job import MacServiceJobError, unload_registered_service

MANAGEMENT_FD = 4
SUCCESS = "AI_DEVICE_REGISTERED_SERVICE_UNLOADED_V1"


def main(argv=None):
    descriptor = MANAGEMENT_FD
    cancelled = False

    def cancel(signum, frame):
        nonlocal cancelled
        cancelled = True

    previous = signal.signal(signal.SIGTERM, cancel)
    try:
        os.set_inheritable(descriptor, False)
        parser = argparse.ArgumentParser(prog="managed-client-service-unload")
        parser.add_argument("--inherit-management-lock", required=True, action="store_true")
        parser.parse_args(argv)
        if sys.platform != "darwin" or os.getuid() != 0 or os.geteuid() != 0:
            raise MacServiceJobError("administrator required")

        def current():
            return management_lock_matches(descriptor, MANAGEMENT_DIRECTORY, owner_uid=0)

        if not current():
            raise MacServiceJobError("management descriptor is unavailable")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not current():
            raise MacServiceJobError("management descriptor changed")

        def guarded():
            return not cancelled and current()

        unload_registered_service(guard_current=guarded)
        if cancelled:
            raise MacServiceJobError("service unload cancelled")
        print(SUCCESS)
    except (MacServiceJobError, OSError, ValueError):
        raise SystemExit("registered service unload unavailable; outcome may be unknown") from None
    finally:
        signal.signal(signal.SIGTERM, previous)
        try:
            os.close(descriptor)
        except OSError:
            pass


if __name__ == "__main__":
    main()
