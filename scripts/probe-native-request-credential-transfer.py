"""Disposable Linux demonstration: connection peer is not each message sender.

The parent connects to a local Unix listener as root, then deliberately passes
that connected endpoint to its child, which drops privileges before sending.
No production helper is
invoked and this does not demonstrate that a real connection is transferred.
"""
import os
import socket
import struct
import sys


def main():
    if (sys.platform != "linux" or os.getpid() != 1 or os.geteuid() != 0
            or os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "request-credential-transfer-v1"):
        raise RuntimeError("disposable Linux root PID1 fixture required")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind("/run/request-credential-transfer-fixture.sock")
    listener.listen(1)
    child = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    child.connect("/run/request-credential-transfer-fixture.sock")
    parent, _ = listener.accept()
    listener.close()
    parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    parent.settimeout(10)
    pid = os.fork()
    if pid == 0:
        try:
            parent.close()
            os.setgroups([])
            os.setgid(12345)
            os.setuid(12345)
            child.sendall(b"fixture request\n")
            child.close()
        finally:
            os._exit(0)
    child.close()
    try:
        peer = struct.unpack("3i", parent.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        data, ancillary, flags, _ = parent.recvmsg(256, socket.CMSG_SPACE(12))
        credentials = [struct.unpack("3i", payload[:12]) for level, kind, payload in ancillary
                       if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS
                       and len(payload) >= 12]
        assert not flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC)
        assert data == b"fixture request\n" and len(credentials) == 1
        assert peer[1] == 0
        assert credentials[0][0] == pid and credentials[0][1:] == (12345, 12345)
        waited, status = os.waitpid(pid, 0)
        assert waited == pid and os.waitstatus_to_exitcode(status) == 0
        print("ROOT_SOCKET_CREATOR_PEERCRED_NONROOT_MESSAGE_SENDER", flush=True)
        print("TRANSFER_FIXTURE_NOT_PRODUCTION_AUTHORIZATION_PROOF", flush=True)
    finally:
        parent.close()


if __name__ == "__main__":
    main()
