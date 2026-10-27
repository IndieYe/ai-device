"""Linux-only isolated probe; observations are not a production authorization protocol."""
import array
import os
import socket
import struct
import subprocess
import sys

CRED = struct.Struct("3i")


def wait_child(pid):
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status


def send_child(sock, data, fd=None):
    pid = os.fork()
    if pid == 0:
        try:
            ancillary = [] if fd is None else [
                (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))
            ]
            assert sock.sendmsg([data], ancillary) == len(data)
            os._exit(0)
        except (OSError, AssertionError):
            os._exit(1)
    return pid


def received(sock, size, ancillary_size):
    data, controls, flags, _ = sock.recvmsg(size, ancillary_size)
    creds = []
    fds = []
    for level, kind, payload in controls:
        if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
            creds.append(CRED.unpack(payload))
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            values = array.array("i")
            values.frombytes(payload[: len(payload) - len(payload) % values.itemsize])
            fds.extend(values)
    return data, flags, creds, fds


def test_supervisor_exit():
    observer, endpoint = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    observer.settimeout(5)
    observer.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    gate_read, gate_write = os.pipe()
    supervisor = os.fork()
    if supervisor == 0:
        try:
            observer.close()
            os.close(gate_write)
            worker = os.fork()
            if worker == 0:
                try:
                    endpoint.sendall(b"worker-alive")
                    assert os.read(gate_read, 1) == b"x"
                    endpoint.close()
                    os.close(gate_read)
                    os._exit(0)
                except (OSError, AssertionError):
                    os._exit(1)
            os.close(gate_read)
            endpoint.close()
            os._exit(0)
        except OSError:
            os._exit(1)
    endpoint.close()
    os.close(gate_read)
    try:
        wait_child(supervisor)
        data, flags, creds, fds = received(observer, 64, socket.CMSG_SPACE(CRED.size))
        assert data == b"worker-alive" and flags == 0 and len(creds) == 1 and not fds
        assert creds[0][0] != supervisor
        print("supervisor exited: inherited endpoint remains live in worker", flush=True)
        os.write(gate_write, b"x")
        os.close(gate_write)
        gate_write = None
        assert observer.recv(1) == b""
    finally:
        if gate_write is not None:
            os.close(gate_write)
        observer.close()


def main():
    if sys.platform != "linux":
        raise RuntimeError("Linux required")
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    a.settimeout(5)
    a.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    try:
        first = send_child(b, b"first")
        second = send_child(b, b"second")
        observed = {}
        for _ in range(2):
            data, flags, creds, fds = received(a, 64, socket.CMSG_SPACE(CRED.size))
            assert flags == 0 and len(creds) == 1 and not fds
            observed[data] = creds[0][0]
        wait_child(first)
        wait_child(second)
        assert observed == {b"first": first, b"second": second}, observed
        print("SEQPACKET: two records preserve actual sender credentials", flush=True)

        pid = send_child(b, b"0123456789")
        data, flags, creds, fds = received(a, 4, socket.CMSG_SPACE(CRED.size))
        wait_child(pid)
        assert data == b"0123" and flags & socket.MSG_TRUNC and creds[0][0] == pid and not fds
        print("SEQPACKET: short payload sets MSG_TRUNC", flush=True)

        pid = send_child(b, b"x")
        data, flags, _, fds = received(a, 8, 0)
        wait_child(pid)
        assert data == b"x" and flags & socket.MSG_CTRUNC and not fds
        print("SEQPACKET: missing ancillary buffer sets MSG_CTRUNC", flush=True)

        read_fd, write_fd = os.pipe()
        try:
            pid = send_child(b, b"fd", read_fd)
            data, flags, creds, fds = received(
                a, 8, socket.CMSG_SPACE(CRED.size) + socket.CMSG_SPACE(array.array("i").itemsize)
            )
            try:
                wait_child(pid)
                assert data == b"fd" and flags == 0 and creds[0][0] == pid and len(fds) == 1
                print("SEQPACKET: unexpected SCM_RIGHTS is delivered and must be closed/rejected", flush=True)
            finally:
                for fd in fds:
                    os.close(fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)

        assert not os.get_inheritable(b.fileno())
        pid = os.fork()
        if pid == 0:
            try:
                os.fstat(b.fileno())
                os._exit(0)
            except OSError:
                os._exit(1)
        wait_child(pid)
        check = "import os,sys; os.fstat(int(sys.argv[1]))"
        no_pass = subprocess.run([sys.executable, "-c", check, str(b.fileno())], capture_output=True, check=False)
        explicit = subprocess.run(
            [sys.executable, "-c", check, str(b.fileno())], pass_fds=(b.fileno(),), capture_output=True, check=False
        )
        assert no_pass.returncode != 0 and explicit.returncode == 0
        print("FD: non-inheritable survives fork, not exec by default; pass_fds deliberately preserves it", flush=True)
        test_supervisor_exit()
        print(f"MESSAGE_BOUNDARIES_OK kernel={os.uname().release}", flush=True)
    finally:
        a.close()
        b.close()


if __name__ == "__main__":
    main()
