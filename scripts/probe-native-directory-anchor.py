"""Demonstrate directory flock boundaries in a disposable Linux environment."""
import fcntl
import os
import sys
import tempfile
from pathlib import Path


def locked(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise
    return fd


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("isolated Linux administrator required")
    with tempfile.TemporaryDirectory(prefix="device-anchor-", dir="/var/lib") as base:
        registry = Path(base) / "registry"
        registry.mkdir()
        lock = registry / "helper-daemon.lock"
        lock.touch()
        old_dir = locked(registry)
        try:
            lock.rename(registry / "old-lock")
            lock.touch()
            try:
                duplicate = locked(registry)
            except BlockingIOError:
                pass
            else:
                os.close(duplicate)
                raise AssertionError("same directory lock did not conflict")
            print("DIRECTORY_LOCK_BLOCKS_FILE_REPLACEMENT", flush=True)
            registry.rename(Path(base) / "old-registry")
            registry.mkdir()
            replacement = locked(registry)
            try:
                assert os.fstat(old_dir).st_ino != os.fstat(replacement).st_ino
                print("DIRECTORY_REPLACEMENT_SPLITS_ANCHOR", flush=True)
            finally:
                os.close(replacement)
        finally:
            os.close(old_dir)
    print("DIRECTORY_ANCHOR_PROBE_OK", flush=True)


if __name__ == "__main__":
    main()
