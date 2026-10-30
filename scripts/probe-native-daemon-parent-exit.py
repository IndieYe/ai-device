"""Probe daemon leases after a forking parent exits in an isolated Linux container."""
import os
import select
import sys
import tempfile
import time

from device_runtime import native_helper as helper


def receive(fd):
    if not select.select([fd], [], [], 5)[0]:
        raise TimeoutError("pipe timeout")
    data = os.read(fd, 1)
    if len(data) != 1:
        raise RuntimeError("unexpected EOF")
    return data


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
    with tempfile.TemporaryDirectory(prefix="device-parent-exit-", dir="/var/lib") as registry:
        ready_r, ready_w = os.pipe()
        release_r, release_w = os.pipe()
        supervisor = os.fork()
        if supervisor == 0:
            try:
                os.close(ready_r)
                os.close(release_w)
                with helper._daemon_lease(registry):
                    worker = os.fork()
                    if worker == 0:
                        os.write(ready_w, b"R")
                        if receive(release_r) != b"X":
                            os._exit(2)
                        os.close(ready_w)
                        os.close(release_r)
                        os._exit(0)
                    os._exit(0)  # Abrupt parent exit; no context-manager cleanup.
            except (OSError, TimeoutError, RuntimeError, helper.HelperError):
                os._exit(1)
        os.close(ready_w)
        os.close(release_r)
        try:
            assert receive(ready_r) == b"R"
            _, status = os.waitpid(supervisor, 0)
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
            assert held(registry), "parent exit released a worker-held lease"
            os.write(release_w, b"X")
            # EOF confirms the worker closed its last copy of the ready pipe.
            assert select.select([ready_r], [], [], 5)[0]
            assert os.read(ready_r, 1) == b""
            deadline = time.monotonic() + 5
            while held(registry):
                if time.monotonic() >= deadline:
                    raise TimeoutError("lease remained held after worker exit")
                time.sleep(0.01)
            print("DAEMON_PARENT_EXIT_WORKER_LEASE_OK", flush=True)
        finally:
            os.close(ready_r)
            os.close(release_w)


if __name__ == "__main__":
    main()
