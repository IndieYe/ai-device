"""Demonstrate inherited flock LOCK_UN in an isolated Linux container only."""
import fcntl
import os
import select
import sys
import tempfile
from pathlib import Path

from device_runtime import native_helper as helper


def receive(fd):
    if not select.select([fd], [], [], 5)[0]:
        raise TimeoutError("peer did not respond")
    result = os.read(fd, 1)
    if len(result) != 1:
        raise RuntimeError("peer closed pipe")
    return result


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("isolated Linux administrator required")
    with tempfile.TemporaryDirectory(prefix="device-unlock-probe-", dir="/var/lib") as registry:
        ready_r, ready_w = os.pipe()
        command_r, command_w = os.pipe()
        pid = None
        try:
            # Use the same two real primitives as _daemon_lease; exposing the
            # anchor here is only for this experiment, not a production API.
            with helper._daemon_anchor(registry) as anchor, helper._daemon_file_lease(
                Path(registry)
            ) as lease:
                pid = os.fork()
                if pid == 0:
                    try:
                        os.close(ready_r)
                        os.close(command_w)
                        fcntl.flock(lease, fcntl.LOCK_UN)
                        os.write(ready_w, b"F")
                        if receive(command_r) != b"D":
                            os._exit(2)
                        fcntl.flock(anchor, fcntl.LOCK_UN)
                        os.write(ready_w, b"D")
                        os._exit(0)
                    except (OSError, TimeoutError, RuntimeError):
                        os._exit(1)
                os.close(ready_w)
                ready_w = None
                os.close(command_r)
                command_r = None
                assert receive(ready_r) == b"F"
                # Both inherited descriptions remain open in the parent.
                os.fstat(lease)
                os.fstat(anchor)
                with helper._daemon_file_lease(Path(registry)):
                    pass
                try:
                    with helper._daemon_lease(registry):
                        raise AssertionError("directory anchor should still block")
                except helper.HelperError as exc:
                    if "already active" not in str(exc):
                        raise
                os.write(command_w, b"D")
                assert receive(ready_r) == b"D"
                os.fstat(lease)
                os.fstat(anchor)
                with helper._daemon_lease(registry):
                    pass
                _, status = os.waitpid(pid, 0)
                pid = None
                if not os.WIFEXITED(status) or os.WEXITSTATUS(status):
                    raise RuntimeError("child failed")
            print("INHERITED_EXPLICIT_UNLOCK_CONFIRMED", flush=True)
        finally:
            for fd in (ready_r, ready_w, command_r, command_w):
                if fd is not None:
                    os.close(fd)
            if pid is not None:
                os.waitpid(pid, 0)


if __name__ == "__main__":
    main()
