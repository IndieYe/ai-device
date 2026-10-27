"""Disposable Linux handshake experiment; not an authorization mechanism."""
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
        raise TimeoutError("control pipe timed out")
    data = os.read(fd, 1)
    if len(data) != 1:
        raise RuntimeError("control pipe closed")
    return data


def observe(receiver, pidfd, worker, challenge):
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
    if poller.poll(0):
        raise ValueError("worker exited")
    record = strict.receive_record(receiver, worker, os.getuid())
    if record != b"READY:" + challenge:
        raise ValueError("challenge mismatch")
    if poller.poll(0):
        raise ValueError("worker exited")
    # This result is a test observation only. Worker death can follow immediately.
    return "observed_not_authorized"


def scenario(mode):
    receiver, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    receiver.settimeout(1)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    gate_r, gate_w = os.pipe()
    worker = os.fork()
    if worker == 0:
        try:
            receiver.close()
            os.close(gate_w)
            challenge = sender.recv(32)
            if len(challenge) != 32:
                os._exit(2)
            if mode == "descendant":
                child = os.fork()
                if child == 0:
                    sender.sendall(b"READY:" + challenge)
                    os._exit(0)
                _, status = os.waitpid(child, 0)
                if not os.WIFEXITED(status) or os.WEXITSTATUS(status):
                    os._exit(3)
            elif mode == "wrong":
                sender.sendall(b"READY:" + b"x" * 32)
            elif mode != "timeout":
                sender.sendall(b"READY:" + challenge)
            if mode == "exited":
                os._exit(0)
            if read_byte(gate_r) != b"X":
                os._exit(4)
            os._exit(0)
        except (OSError, TimeoutError, RuntimeError):
            os._exit(1)
    sender.close()
    os.close(gate_r)
    pidfd = None
    try:
        # The worker is blocked on the challenge, so it cannot exit before pidfd_open.
        pidfd = os.pidfd_open(worker)
        challenge = os.urandom(32)
        receiver.sendall(challenge)
        if mode == "exited":
            poller = select.poll()
            poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
            assert poller.poll(5000)
        try:
            result = observe(receiver, pidfd, worker, challenge)
        except (ValueError, TimeoutError) as exc:
            if mode == "good":
                raise
            if mode == "wrong":
                assert str(exc) == "challenge mismatch"
            elif mode == "descendant":
                assert str(exc) == "record refused"
            elif mode == "exited":
                assert str(exc) == "worker exited"
            else:
                assert isinstance(exc, TimeoutError)
        else:
            assert mode == "good" and result == "observed_not_authorized"
        if mode != "exited":
            os.write(gate_w, b"X")
        _, status = os.waitpid(worker, 0)
        worker = None
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    finally:
        receiver.close()
        os.close(gate_w)
        if pidfd is not None:
            os.close(pidfd)
        if worker is not None:
            os.waitpid(worker, 0)


def main():
    if sys.platform != "linux" or not hasattr(os, "pidfd_open"):
        raise RuntimeError("isolated Linux pidfd required")
    for mode in ("good", "wrong", "descendant", "exited", "timeout"):
        scenario(mode)
    print("PIDFD_RECORD_HANDSHAKE_FIXTURE_OK", flush=True)


if __name__ == "__main__":
    main()
