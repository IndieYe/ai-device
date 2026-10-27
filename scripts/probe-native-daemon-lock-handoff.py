"""Isolated flock handoff probe, not a production lock protocol."""
import fcntl
import os
import select
import sys
import tempfile


def read_one(fd):
    ready, _, _ = select.select([fd], [], [], 5)
    if not ready:
        raise TimeoutError("probe timed out")
    data = os.read(fd, 1)
    if len(data) != 1:
        raise RuntimeError("probe pipe closed")
    return data


def try_lock(path):
    fd = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    finally:
        os.close(fd)


def main():
    if sys.platform != "linux":
        raise RuntimeError("Linux required")
    with tempfile.TemporaryDirectory(prefix="device-flock-probe-") as root:
        path = os.path.join(root, "daemon.lock")
        lease = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not try_lock(path), "different open description unexpectedly locked"
        ready_r, ready_w = os.pipe()
        release_r, release_w = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(ready_r)
                os.close(release_w)
                # Inherited lease is the same open file description.
                os.write(ready_w, b"R")
                assert read_one(release_r) == b"X"
                os.close(lease)
                os.close(ready_w)
                os.close(release_r)
                os._exit(0)
            except (OSError, AssertionError, TimeoutError, RuntimeError):
                os._exit(1)
        os.close(ready_w)
        os.close(release_r)
        try:
            assert read_one(ready_r) == b"R"
            os.close(lease)
            lease = None
            assert not try_lock(path), "lock lost while child holds inherited description"
            os.write(release_w, b"X")
            _, status = os.waitpid(pid, 0)
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
            assert try_lock(path), "lock remained after both inherited references closed"
            print("DAEMON_FLOCK_HANDOFF_OK: independent open blocked; child retains lock after parent close; lock released after child close", flush=True)
        finally:
            if lease is not None:
                os.close(lease)
            os.close(ready_r)
            os.close(release_w)


if __name__ == "__main__":
    main()
