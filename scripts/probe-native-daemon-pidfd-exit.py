"""Probe pidfd-observed parent exit with real daemon leases; never production code."""
import os
import select
import sys
import tempfile
import time

from device_runtime import native_helper as helper


def receive(fd):
    if not select.select([fd], [], [], 5)[0]:
        raise TimeoutError("pipe timeout")
    value = os.read(fd, 1)
    if len(value) != 1:
        raise RuntimeError("unexpected EOF")
    return value


def held(registry):
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
    with tempfile.TemporaryDirectory(prefix="device-pidfd-lease-", dir="/var/lib") as registry:
        ready_r, ready_w = os.pipe()
        release_r, release_w = os.pipe()
        supervisor = os.fork()
        if supervisor == 0:
            try:
                os.close(ready_r)
                os.close(release_w)
                parent_fd = os.pidfd_open(os.getpid())
                with helper._daemon_lease(registry):
                    worker = os.fork()
                    if worker == 0:
                        poller = select.poll()
                        poller.register(parent_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
                        if poller.poll(0):
                            os._exit(2)
                        os.write(ready_w, b"R")
                        if not poller.poll(5000):
                            os._exit(3)
                        os.write(ready_w, b"D")
                        os._exit(0)
                    if receive(release_r) != b"X":
                        os._exit(4)
                    os._exit(0)
            except (OSError, TimeoutError, RuntimeError, helper.HelperError):
                os._exit(1)
        os.close(ready_w)
        os.close(release_r)
        try:
            assert receive(ready_r) == b"R"
            assert held(registry), "live worker did not hold daemon lease"
            os.write(release_w, b"X")
            _, status = os.waitpid(supervisor, 0)
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
            assert receive(ready_r) == b"D"
            assert select.select([ready_r], [], [], 5)[0]
            assert os.read(ready_r, 1) == b""
            deadline = time.monotonic() + 5
            while held(registry):
                if time.monotonic() >= deadline:
                    raise TimeoutError("daemon lease did not release")
                time.sleep(0.01)
            print("PIDFD_PARENT_EXIT_DAEMON_LEASE_OK", flush=True)
        finally:
            os.close(ready_r)
            os.close(release_w)


if __name__ == "__main__":
    main()
