"""Demonstrate pidfd check-to-action race with daemon lease; no external action."""
import os
import select
import sys
import tempfile

from device_runtime import native_helper as helper


def receive(fd):
    if not select.select([fd], [], [], 5)[0]:
        raise TimeoutError("pipe timeout")
    data = os.read(fd, 1)
    if len(data) != 1:
        raise RuntimeError("unexpected EOF")
    return data


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("isolated Linux administrator required")
    with tempfile.TemporaryDirectory(prefix="device-pidfd-race-", dir="/var/lib") as registry:
        ready_r, ready_w = os.pipe()
        parent_r, parent_w = os.pipe()
        worker_r, worker_w = os.pipe()
        supervisor = os.fork()
        if supervisor == 0:
            try:
                os.close(ready_r)
                os.close(parent_w)
                os.close(worker_w)
                parent_fd = os.pidfd_open(os.getpid())
                with helper._daemon_lease(registry):
                    worker = os.fork()
                    if worker == 0:
                        poller = select.poll()
                        poller.register(parent_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
                        if poller.poll(0):
                            os._exit(2)
                        os.write(ready_w, b"R")  # Parent was alive at check.
                        if receive(worker_r) != b"A":
                            os._exit(3)
                        if not poller.poll(0):
                            os._exit(4)
                        # Simulated action only: publish an observation byte.
                        # No re-check is inserted into the hypothetical action.
                        os.write(ready_w, b"A")
                        os._exit(0)
                    if receive(parent_r) != b"X":
                        os._exit(5)
                    os._exit(0)
            except (OSError, TimeoutError, RuntimeError, helper.HelperError):
                os._exit(1)
        for fd in (ready_w, parent_r, worker_r):
            os.close(fd)
        try:
            assert receive(ready_r) == b"R"
            os.write(parent_w, b"X")
            _, status = os.waitpid(supervisor, 0)
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
            os.write(worker_w, b"A")
            assert receive(ready_r) == b"A"
            assert select.select([ready_r], [], [], 5)[0]
            assert os.read(ready_r, 1) == b""
            print("PIDFD_CHECK_THEN_ACTION_RACE_CONFIRMED", flush=True)
        finally:
            for fd in (ready_r, parent_w, worker_w):
                os.close(fd)


if __name__ == "__main__":
    main()
