"""Internal fixed-fd start helper; not exposed by inspection packages.

A trusted native parent must already hold management flock continuously and
pass the same open-file-description as fd4. This process NEVER unlocks it:
LOCK_UN would also release the parent's shared flock. Close only our copy.
No path, UID, target or command can be selected through argv/environment.
"""

import argparse
import fcntl
import os
import signal
import sys

from .client_macos_management import MANAGEMENT_DIRECTORY, management_lock_matches
from .client_macos_query import _capture_cancelled
from .client_macos_start import MacStartError, _request_registered_start_held

MANAGEMENT_FD = 4
SUCCESS = "AI_DEVICE_REGISTERED_START_REQUESTED_V1"


def main(argv=None):
    descriptor = MANAGEMENT_FD
    cancelled = False

    def cancel(signum, frame):
        nonlocal cancelled
        cancelled = True

    previous = signal.signal(signal.SIGTERM, cancel)
    token = _capture_cancelled.set(lambda: cancelled)
    try:
        # Before parsing or any possible subprocess, prohibit further inheritance.
        os.set_inheritable(descriptor, False)
        parser = argparse.ArgumentParser(prog="managed-client-start")
        parser.add_argument("--inherit-management-lock", required=True, action="store_true")
        parser.parse_args(argv)
        if sys.platform != "darwin" or os.getuid() != 0 or os.geteuid() != 0:
            raise MacStartError("administrator required")

        def current():
            return management_lock_matches(descriptor, MANAGEMENT_DIRECTORY, owner_uid=0)

        if not current():
            raise MacStartError("management descriptor is unavailable")
        # Same inherited open-file-description: this does not acquire a second
        # lock or drop the parent's lock. Trusted caller supplies a held fd.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not current():
            raise MacStartError("management descriptor changed")

        def guarded():
            return not cancelled and current()

        _request_registered_start_held(guard_current=guarded)
        if cancelled:
            raise MacStartError("start cancelled")
        print(SUCCESS)
    except (MacStartError, OSError, ValueError):
        raise SystemExit("managed client start unavailable; outcome may be unknown") from None
    finally:
        _capture_cancelled.reset(token)
        signal.signal(signal.SIGTERM, previous)
        try:
            os.close(descriptor)
        except OSError:
            pass


if __name__ == "__main__":
    main()
