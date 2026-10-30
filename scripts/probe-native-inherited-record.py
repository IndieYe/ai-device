"""Linux-only experiment: inherited socket endpoint does not inherit sender identity."""
import importlib.util
import os
import socket
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("strict_record", Path(__file__).with_name("probe-native-strict-record.py"))
strict = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strict)


def main():
    if sys.platform != "linux" or not hasattr(os, "fork"):
        raise RuntimeError("isolated Linux fork required")
    receiver, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    receiver.settimeout(5)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    control_r, control_w = os.pipe()
    worker = os.fork()
    if worker == 0:
        try:
            receiver.close()
            os.close(control_w)
            sender.sendall(b"worker")
            if os.read(control_r, 1) != b"X":
                os._exit(2)
            descendant = os.fork()
            if descendant == 0:
                sender.sendall(b"descendant")
                os._exit(0)
            _, status = os.waitpid(descendant, 0)
            if not os.WIFEXITED(status) or os.WEXITSTATUS(status):
                os._exit(3)
            os._exit(0)
        except (OSError, RuntimeError):
            os._exit(1)
    sender.close()
    os.close(control_r)
    try:
        assert strict.receive_record(receiver, worker, os.getuid()) == b"worker"
        os.write(control_w, b"X")
        try:
            strict.receive_record(receiver, worker, os.getuid())
        except ValueError as exc:
            assert str(exc) == "record refused"
        else:
            raise AssertionError("descendant was accepted as worker")
        _, status = os.waitpid(worker, 0)
        worker = None
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        print("INHERITED_ENDPOINT_DESCENDANT_RECORD_REJECTED", flush=True)
    finally:
        receiver.close()
        os.close(control_w)
        if worker is not None:
            os.waitpid(worker, 0)


if __name__ == "__main__":
    main()
