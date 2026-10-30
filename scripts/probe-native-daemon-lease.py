"""Test actual daemon lease across fork in a disposable Linux container."""
import os
import select
import sys
import tempfile

from device_runtime import native_helper as helper


def read_byte(fd):
    if not select.select([fd], [], [], 5)[0]:
        raise TimeoutError("child did not respond")
    data = os.read(fd, 1)
    if len(data) != 1:
        raise RuntimeError("unexpected EOF")
    return data


def refused(registry):
    try:
        with helper._daemon_lease(registry):
            return False
    except helper.HelperError as exc:
        if "already active" not in str(exc):
            raise
        return True


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("isolated Linux administrator required")
    with tempfile.TemporaryDirectory(prefix="device-daemon-lease-", dir="/var/lib") as registry:
        ready_r, ready_w = os.pipe()
        release_r, release_w = os.pipe()
        pid = None
        try:
            with helper._daemon_lease(registry) as lease:
                assert refused(registry)
                pid = os.fork()
                if pid == 0:
                    try:
                        os.close(ready_r)
                        os.close(release_w)
                        os.fstat(lease)
                        os.write(ready_w, b"R")
                        assert read_byte(release_r) == b"X"
                        os.close(lease)
                        os._exit(0)
                    except (OSError, AssertionError, TimeoutError, RuntimeError):
                        os._exit(1)
                os.close(ready_w)
                ready_w = None
                os.close(release_r)
                release_r = None
                assert read_byte(ready_r) == b"R"
            assert refused(registry), "parent close released inherited lease"
            os.write(release_w, b"X")
            _, status = os.waitpid(pid, 0)
            pid = None
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
            with helper._daemon_lease(registry):
                pass
            print("ACTUAL_DAEMON_LEASE_FORK_OK", flush=True)
        finally:
            for fd in (ready_r, ready_w, release_r, release_w):
                if fd is not None:
                    os.close(fd)
            if pid is not None:
                os.waitpid(pid, 0)


if __name__ == "__main__":
    main()
