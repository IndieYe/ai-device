"""Isolated Linux experiment: per-record credentials plus child pidfd, not authorization."""
import importlib.util
import os
import select
import socket
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "strict_record", Path(__file__).with_name("probe-native-strict-record.py")
)
strict = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strict)


def read_byte(fd):
    if not select.select([fd], [], [], 5)[0]:
        raise TimeoutError("control pipe timeout")
    value = os.read(fd, 1)
    if len(value) != 1:
        raise RuntimeError("unexpected control pipe EOF")
    return value


def main():
    if sys.platform != "linux" or not hasattr(os, "pidfd_open"):
        raise RuntimeError("isolated Linux pidfd required")
    receiver, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    receiver.settimeout(5)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    control_r, control_w = os.pipe()
    worker = os.fork()
    if worker == 0:
        try:
            receiver.close()
            os.close(control_w)
            if read_byte(control_r) != b"A":
                os._exit(2)
            sender.sendall(b"worker")
            if read_byte(control_r) != b"B":
                os._exit(3)
            descendant = os.fork()
            if descendant == 0:
                sender.sendall(b"descendant")
                os._exit(0)
            _, status = os.waitpid(descendant, 0)
            if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
                os._exit(4)
            if read_byte(control_r) != b"C":
                os._exit(5)
            sender.sendall(b"queued-before-exit")
            os._exit(0)
        except (OSError, TimeoutError, RuntimeError):
            os._exit(1)
    sender.close()
    os.close(control_r)
    pidfd = None
    try:
        # The child waits for A, so its PID cannot be recycled before pidfd_open.
        pidfd = os.pidfd_open(worker)
        poller = select.poll()
        poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        assert not poller.poll(0)
        os.write(control_w, b"A")
        assert strict.receive_record(receiver, worker, os.getuid()) == b"worker"
        assert not poller.poll(0)
        os.write(control_w, b"B")
        try:
            strict.receive_record(receiver, worker, os.getuid())
        except ValueError as exc:
            assert str(exc) == "record refused"
        else:
            raise AssertionError("descendant accepted as worker")
        os.write(control_w, b"C")
        assert poller.poll(5000), "worker exit not observed by pidfd"
        # An otherwise valid message can remain queued after sender exit.
        assert strict.receive_record(receiver, worker, os.getuid()) == b"queued-before-exit"
        assert poller.poll(0), "dead worker must fail an explicit liveness gate"
        _, status = os.waitpid(worker, 0)
        worker = None
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        print("PIDFD_CREDENTIALS_DESCENDANT_REJECTED_QUEUED_AFTER_EXIT", flush=True)
    finally:
        receiver.close()
        os.close(control_w)
        if pidfd is not None:
            os.close(pidfd)
        if worker is not None:
            os.waitpid(worker, 0)


if __name__ == "__main__":
    main()
