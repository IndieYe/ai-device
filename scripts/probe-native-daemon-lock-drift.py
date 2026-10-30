"""Demonstrate post-acquisition pathname replacement; disposable Linux only."""
import os
import sys
import tempfile
from pathlib import Path

from device_runtime import native_helper as helper


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("isolated Linux administrator required")
    with tempfile.TemporaryDirectory(prefix="device-lock-drift-", dir="/var/lib") as directory:
        registry = Path(directory)
        path = registry / "helper-daemon.lock"
        with helper._daemon_lease(registry) as old:
            old_ino = os.fstat(old).st_ino
            path.rename(registry / "retained-original.lock")
            # Authorized test-only root simulates accidental replacement.
            with helper._daemon_lease(registry) as new:
                assert os.fstat(new).st_ino != old_ino
                print("POST_ACQUISITION_SPLIT_REPRODUCED: two distinct locked inodes", flush=True)
    print("LOCK_DRIFT_PROBE_OK: acquisition check alone cannot prevent later drift", flush=True)


if __name__ == "__main__":
    main()
