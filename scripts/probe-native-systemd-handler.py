"""Disposable systemd counterexample: peer observation is not handler proof."""
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

ENDPOINT = "/run/device-peer-handler-probe.sock"
CREDENTIALS = struct.Struct("3i")


def guard():
    if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes" or os.geteuid() != 0:
        raise RuntimeError("disposable administrator context required")
    if Path("/proc/1/comm").read_text().strip() != "systemd":
        raise RuntimeError("isolated systemd required")


def serve():
    guard()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(ENDPOINT)
        server.listen(1)
        child = os.fork()
        if child == 0:
            try:
                connection, _ = server.accept()
                with connection:
                    if connection.recv(16) != b"observe":
                        os._exit(2)
                    connection.sendall(b"handled")
                os._exit(0)
            except OSError:
                os._exit(3)
        _, status = os.waitpid(child, 0)
        if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
            raise RuntimeError("fixture handler failed")
        # Remain the live systemd MainPID through the observation post-check.
        while True:
            time.sleep(1)


def observe():
    guard()
    from device_runtime import native_helper as helper

    pid = int(subprocess.check_output(
        ["systemctl", "show", "ai-device-native-helper.service", "-p", "MainPID", "--value"],
        text=True, timeout=10,
    ).strip())
    deadline = time.monotonic() + 10
    while not Path(ENDPOINT).exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        connection.connect(ENDPOINT)
        with helper.scoped_helper_unit_peer(connection) as observed:
            assert observed["pid"] == pid and pid > 1
            assert observed["dispatch_allowed"] is False
            assert observed["loaded_code_verified"] is False
            connection.sendall(b"observe")
            data, ancillary, flags, _ = connection.recvmsg(16, socket.CMSG_SPACE(CREDENTIALS.size))
            assert flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) == 0
            credentials = [CREDENTIALS.unpack(value) for level, kind, value in ancillary
                           if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS
                           and len(value) == CREDENTIALS.size]
            assert data == b"handled" and len(credentials) == 1
            sender_pid, sender_uid, _ = credentials[0]
            assert sender_uid == 0 and sender_pid > 1 and sender_pid != pid
    print("SYSTEMD_PEER_MAINPID_DIFFERS_FROM_HANDLER_OBSERVED_NOT_AUTHORIZED", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"serve", "observe"}:
        raise SystemExit("expected serve or observe")
    (serve if sys.argv[1] == "serve" else observe)()
