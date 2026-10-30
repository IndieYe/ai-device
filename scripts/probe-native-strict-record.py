"""Linux isolated strict-record prototype; not used by production."""
import array
import os
import socket
import struct
import sys

CRED = struct.Struct("3i")
MAX_RECORD = 64
MAX_RIGHTS = 8


def receive_record(sock, expected_pid, expected_uid):
    data, controls, flags, _ = sock.recvmsg(
        MAX_RECORD + 1,
        socket.CMSG_SPACE(CRED.size) + socket.CMSG_SPACE(MAX_RIGHTS * array.array("i").itemsize),
        getattr(socket, "MSG_CMSG_CLOEXEC", 0),
    )
    credentials = []
    received_fds = []
    unexpected = False
    try:
        for level, kind, payload in controls:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds = array.array("i")
                fds.frombytes(payload[: len(payload) - len(payload) % fds.itemsize])
                received_fds.extend(fds)
                unexpected = True
            elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(payload) == CRED.size:
                credentials.append(CRED.unpack(payload))
            else:
                unexpected = True
        if (
            flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
            or not data
            or len(data) > MAX_RECORD
            or unexpected
            or len(credentials) != 1
            or credentials[0][:2] != (expected_pid, expected_uid)
        ):
            raise ValueError("record refused")
        return data
    finally:
        for fd in received_fds:
            os.close(fd)


def wait(pid):
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status


def child_send(sock, data, fd=None):
    pid = os.fork()
    if pid == 0:
        try:
            ancillary = [] if fd is None else [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))]
            assert sock.sendmsg([data], ancillary) == len(data)
            os._exit(0)
        except (OSError, AssertionError):
            os._exit(1)
    return pid


def fd_count():
    return len(os.listdir("/proc/self/fd"))


def main():
    if sys.platform != "linux":
        raise RuntimeError("Linux required")
    receiver, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    receiver.settimeout(5)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    try:
        pid = child_send(sender, b"ok")
        assert receive_record(receiver, pid, os.getuid()) == b"ok"
        wait(pid)
        pid = child_send(sender, b"wrong")
        try:
            receive_record(receiver, os.getpid(), os.getuid())
            raise AssertionError("wrong sender accepted")
        except ValueError:
            pass
        wait(pid)
        pid = child_send(sender, b"x" * (MAX_RECORD + 10))
        try:
            receive_record(receiver, pid, os.getuid())
            raise AssertionError("truncated record accepted")
        except ValueError:
            pass
        wait(pid)
        r, w = os.pipe()
        try:
            before = fd_count()
            pid = child_send(sender, b"fd", r)
            try:
                receive_record(receiver, pid, os.getuid())
                raise AssertionError("SCM_RIGHTS accepted")
            except ValueError:
                pass
            wait(pid)
            assert fd_count() == before, "received descriptor leaked"
        finally:
            os.close(r)
            os.close(w)
        print("STRICT_RECORD_PROBE_OK: valid sender accepted; wrong sender, truncation and rights refused; no fd leak", flush=True)
    finally:
        receiver.close()
        sender.close()


if __name__ == "__main__":
    main()
