"""Isolated Linux probe: Unix peer identity across fork, not an authorization test."""
import os
import select
import socket
import struct
import sys
import tempfile

CRED = struct.Struct("3i")


def peer(sock):
    return CRED.unpack(sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, CRED.size))


def reap(pid):
    _, status = os.waitpid(pid, 0)
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        raise RuntimeError(f"child {pid} failed, status={status}")


def fork_send(sock):
    pid = os.fork()
    if pid == 0:
        try:
            sock.sendall(b"x")
            os._exit(0)
        except (OSError, AssertionError, RuntimeError, ValueError):
            os._exit(1)
    return pid


def test_socketpair():
    parent_pid = os.getpid()
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    a.settimeout(5)
    a.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    child = fork_send(b)
    b.close()
    try:
        data, controls, flags, _ = a.recvmsg(1, socket.CMSG_SPACE(CRED.size))
        assert data == b"x" and not flags & socket.MSG_CTRUNC
        actual = [CRED.unpack(payload[:CRED.size]) for level, kind, payload in controls
                  if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
        assert len(actual) == 1 and actual[0][0] == child, actual
        pair_peer = peer(a)[0]
        assert pair_peer == parent_pid, (pair_peer, parent_pid, child)
        print(f"socketpair: SO_PEERCRED={pair_peer} creator={parent_pid}, SCM_CREDENTIALS sender={actual[0][0]} child={child}", flush=True)
    finally:
        a.close()
        reap(child)


def test_listener(path, child_binds):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(5)
    if not child_binds:
        server.bind(path)
        server.listen(1)
    ready_r, ready_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(ready_r)
            if child_binds:
                server.bind(path)
                server.listen(1)
            os.write(ready_w, b"r")
            os.close(ready_w)
            conn, _ = server.accept()
            with conn:
                conn.settimeout(5)
                assert conn.recv(1) == b"x"
                conn.sendall(b"y")
                assert conn.recv(1) == b"q"
            server.close()
            os._exit(0)
        except (OSError, AssertionError, RuntimeError, ValueError):
            os._exit(1)
    os.close(ready_w)
    server.close()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    reaped = False
    try:
        assert os.read(ready_r, 1) == b"r"
        os.close(ready_r)
        client.connect(path)
        actual = peer(client)[0]
        expected = pid if child_binds else os.getpid()
        assert actual == expected, (actual, expected)
        raw = client.getsockopt(socket.SOL_SOCKET, getattr(socket, "SO_PEERPIDFD", 77), 4)
        fd = struct.unpack("i", raw)[0]
        try:
            with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as f:
                fields = dict(line.rstrip().split(":", 1) for line in f if ":" in line)
            assert int(fields["Pid"].strip()) == expected, fields
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            assert not poller.poll(0), "peer already exited"
            client.sendall(b"x")
            assert client.recv(1) == b"y"
            print(f"listener {'child-bound' if child_binds else 'parent-bound'}: peer={actual} pidfd={fields['Pid'].strip()} child={pid}", flush=True)
            client.sendall(b"q")
            reap(pid)
            reaped = True
            if child_binds:
                assert poller.poll(1000), "pidfd did not report child exit"
                print("child-bound pidfd: exit observed", flush=True)
        finally:
            os.close(fd)
    finally:
        client.close()
        if not reaped:
            reap(pid)


def test_inherited_listener(path):
    ready_r, ready_w = os.pipe()
    creator = os.fork()
    if creator == 0:
        try:
            os.close(ready_r)
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.settimeout(5)
            server.bind(path)
            server.listen(1)
            worker = os.fork()
            if worker == 0:
                try:
                    os.close(ready_w)
                    conn, _ = server.accept()
                    with conn:
                        conn.sendall(str(os.getpid()).encode())
                    os._exit(0)
                except (OSError, AssertionError, RuntimeError, ValueError):
                    os._exit(1)
            os.write(ready_w, f"{worker}\n".encode())
            os.close(ready_w)
            reap(worker)
            server.close()
            os._exit(0)
        except (OSError, AssertionError, RuntimeError, ValueError):
            os._exit(1)
    os.close(ready_w)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    try:
        worker = int(os.read(ready_r, 32).strip())
        os.close(ready_r)
        client.connect(path)
        actual = peer(client)[0]
        responder = int(client.recv(32))
        assert actual == creator and responder == worker and creator != worker
        print(f"inherited listener: peer={actual} creator={creator}, actual responder={responder}", flush=True)
    finally:
        client.close()
        reap(creator)


def test_stream_sender_credentials():
    receiver, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    receiver.settimeout(5)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    pid = fork_send(sender)
    sender.close()
    try:
        data, controls, flags, _ = receiver.recvmsg(1, socket.CMSG_SPACE(CRED.size))
        assert data == b"x" and not flags & socket.MSG_CTRUNC
        creds = [CRED.unpack(payload[:CRED.size]) for level, kind, payload in controls
                 if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
        assert len(creds) == 1 and creds[0][0] == pid, creds
        assert peer(receiver)[0] == os.getpid()
        print(f"stream SCM_CREDENTIALS sender={creds[0][0]} child={pid}, peer creator={peer(receiver)[0]}", flush=True)
    finally:
        receiver.close()
        reap(pid)


def main():
    if sys.platform != "linux" or not hasattr(os, "fork"):
        raise RuntimeError("Linux fork environment required")
    print(f"kernel={os.uname().release} pid={os.getpid()}", flush=True)
    test_socketpair()
    test_stream_sender_credentials()
    with tempfile.TemporaryDirectory(prefix="device-peer-probe-") as temp:
        test_listener(os.path.join(temp, "parent.sock"), False)
        test_listener(os.path.join(temp, "child.sock"), True)
        test_inherited_listener(os.path.join(temp, "inherited.sock"))
    print("PEER_PROBE_OK", flush=True)


if __name__ == "__main__":
    main()
