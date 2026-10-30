"""Unwired Linux credential/framing experiment, run only in disposable PID1.

Not a production authentication protocol: SCM credentials identify senders of
received bytes, not loaded code, a business action, or a systemd invocation.
"""
import array
import os
import socket
import struct
import sys
import time

MAX_FRAME = 256


class Refused(ValueError):
    pass


def receive_root_frame(connection, *, after_chunk=None, deadline=None):
    """One newline-terminated frame per connection; require same root sender per recv.

    The test controls connection setup and enables SO_PASSCRED before any data.
    The caller must set a finite socket timeout and close the connection afterward.
    Bytes after the first newline in the same read are rejected. Later bytes are
    never processed, because the connection is closed after one response.
    """
    data = bytearray()
    sender = None
    while True:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Refused("frame deadline expired")
            connection.settimeout(remaining)
        chunk, ancillary, flags, _ = connection.recvmsg(64, socket.CMSG_SPACE(12))
        # Unexpected SCM_RIGHTS can install descriptors even on a refused frame.
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                descriptors = array.array("i")
                descriptors.frombytes(payload[:len(payload) - len(payload) % descriptors.itemsize])
                for fd in descriptors:
                    os.close(fd)
        if flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC) or not chunk:
            raise Refused("truncated ancillary or incomplete frame")
        if len(ancillary) != 1:
            raise Refused("one credential required")
        level, kind, payload = ancillary[0]
        if level != socket.SOL_SOCKET or kind != socket.SCM_CREDENTIALS or len(payload) != 12:
            raise Refused("invalid credentials")
        identity = struct.unpack("3i", payload)
        if identity[0] <= 0 or identity[1] != 0 or (sender is not None and sender != identity):
            raise Refused("sender changed or is not root")
        sender = identity
        data.extend(chunk)
        if len(data) > MAX_FRAME:
            raise Refused("frame too large")
        if b"\n" in data:
            if not data.endswith(b"\n") or data.count(b"\n") != 1:
                raise Refused("trailing or multiple frames")
            return bytes(data), sender
        if after_chunk:
            after_chunk()


def run_case(kind):
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    left.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    left.settimeout(5)
    read_ack, write_ack = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            left.close()
            os.close(write_ack)
            if kind == "nonroot":
                os.setgroups([])
                os.setgid(12345)
                os.setuid(12345)
            if kind in ("root-split", "mixed"):
                right.sendall(b"first-")
                if os.read(read_ack, 1) != b"x":
                    os._exit(2)
                if kind == "mixed":
                    os.setgroups([])
                    os.setgid(12345)
                    os.setuid(12345)
                right.sendall(b"second\n")
            elif kind == "oversize":
                right.sendall(b"x" * (MAX_FRAME + 1) + b"\n")
            elif kind == "trailing":
                right.sendall(b"one\ntwo\n")
            elif kind == "incomplete":
                right.sendall(b"unfinished")
            else:
                right.sendall(b"request\n")
            right.close()
            os._exit(0)
        except (OSError, ValueError):
            os._exit(2)
    right.close()
    os.close(read_ack)
    try:
        try:
            frame, sender = receive_root_frame(left, after_chunk=lambda: os.write(write_ack, b"x"))
            assert kind == "root-split" and frame == b"first-second\n"
            assert sender == (pid, 0, 0)
        except Refused:
            assert kind in {"mixed", "nonroot", "oversize", "trailing", "incomplete"}
        waited, status = os.waitpid(pid, 0)
        assert waited == pid and os.waitstatus_to_exitcode(status) == 0
    finally:
        os.close(write_ack)
        left.close()


def check_invalid_control():
    class FakeConnection:
        def __init__(self, ancillary, flags):
            self.ancillary = ancillary
            self.flags = flags

        def recvmsg(self, size, control_size):
            return b"request\n", self.ancillary, self.flags, None

    for ancillary, flags in (([], 0), ([], socket.MSG_CTRUNC),
                             ([(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, b"short")], 0)):
        try:
            receive_root_frame(FakeConnection(ancillary, flags))
        except Refused:
            continue
        raise AssertionError("invalid control accepted")
    print("MISSING_TRUNCATED_AND_MALFORMED_CONTROL_REFUSED", flush=True)


def check_transferred_fd():
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    left.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    left.settimeout(5)
    fd = os.open("/dev/null", os.O_RDONLY)
    try:
        before = len(os.listdir("/proc/self/fd"))
        right.sendmsg([b"request\n"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))])
        try:
            receive_root_frame(left)
        except Refused:
            pass
        else:
            raise AssertionError("unexpected descriptor accepted")
        assert len(os.listdir("/proc/self/fd")) == before
        print("UNEXPECTED_RIGHTS_REFUSED_WITHOUT_DESCRIPTOR_LEAK", flush=True)
    finally:
        os.close(fd)
        left.close()
        right.close()


def main():
    if (sys.platform != "linux" or os.geteuid() != 0 or os.getpid() != 1
            or os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "request-frame-credentials-v1"):
        raise RuntimeError("disposable root Linux PID1 fixture required")
    for case in ("root-split", "mixed", "nonroot", "oversize", "trailing", "incomplete"):
        run_case(case)
        print(f"FRAME_CASE_{case.upper().replace('-', '_')}_OK", flush=True)
    check_invalid_control()
    check_transferred_fd()
    print("UNWIRED_FIXTURE_NOT_HANDLER_OR_INVOCATION_ATTESTATION", flush=True)


if __name__ == "__main__":
    main()
