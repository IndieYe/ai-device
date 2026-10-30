"""Isolated fork/pidfd lifecycle experiment; never used for production dispatch."""
import os
import select
import socket
import struct
import sys

CRED = struct.Struct("3i")


def alive(fd):
    poller = select.poll()
    poller.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
    return not poller.poll(0)


def read_exact(fd, count):
    data = b""
    while len(data) < count:
        ready, _, _ = select.select([fd], [], [], 5)
        if not ready:
            raise TimeoutError("probe timed out")
        part = os.read(fd, count - len(data))
        if not part:
            raise RuntimeError("unexpected pipe EOF")
        data += part
    return data


def recv_credential(sock):
    sock.settimeout(5)
    data, anc, flags, _ = sock.recvmsg(8, socket.CMSG_SPACE(CRED.size))
    creds = [CRED.unpack(value) for level, kind, value in anc
             if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(value) == CRED.size]
    if flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC) or len(creds) != 1:
        raise RuntimeError("untrusted record")
    return data, creds[0]


def wait_ok(pid):
    _, status = os.waitpid(pid, 0)
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        raise RuntimeError(f"child failed: {status}")


def run_scenario(supervisor_exits):
    ready_r, ready_w = os.pipe()
    gate_r, gate_w = os.pipe()
    result_r, result_w = os.pipe()
    supervisor = os.fork()
    if supervisor == 0:
        try:
            os.close(ready_r)
            os.close(gate_w)
            os.close(result_r)
            parent_pid = os.getpid()
            parent_fd = os.pidfd_open(parent_pid)
            control, worker_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            control.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            worker = os.fork()
            if worker == 0:
                try:
                    control.close()
                    os.close(ready_w)
                    worker_end.sendall(b"ready")
                    assert read_exact(gate_r, 1) == b"G"
                    verdict = b"A" if alive(parent_fd) else b"R"
                    os.write(result_w, verdict)
                    worker_end.close()
                    os.close(parent_fd)
                    os._exit(0)
                except (OSError, AssertionError, RuntimeError, TimeoutError):
                    os._exit(1)
            worker_end.close()
            os.close(gate_r)
            os.close(result_w)
            worker_fd = os.pidfd_open(worker)
            data, creds = recv_credential(control)
            assert data == b"ready" and creds[:2] == (worker, os.getuid())
            assert alive(worker_fd) and alive(parent_fd)
            os.write(ready_w, struct.pack("i", worker))
            os.close(ready_w)
            control.close()
            if not supervisor_exits:
                wait_ok(worker)
            os.close(worker_fd)
            os.close(parent_fd)
            os._exit(0)
        except (OSError, AssertionError, RuntimeError, TimeoutError):
            os._exit(1)
    os.close(ready_w)
    os.close(gate_r)
    os.close(result_w)
    try:
        worker = struct.unpack("i", read_exact(ready_r, 4))[0]
        if supervisor_exits:
            wait_ok(supervisor)
        os.write(gate_w, b"G")
        verdict = read_exact(result_r, 1)
        assert verdict == (b"R" if supervisor_exits else b"A"), verdict
        if supervisor_exits:
            # In this isolated probe Python is PID 1 and adopts the orphan.
            wait_ok(worker)
        else:
            wait_ok(supervisor)
        print(f"supervisor {'exited' if supervisor_exits else 'alive'}: verdict={verdict.decode()}", flush=True)
    finally:
        os.close(ready_r)
        os.close(gate_w)
        os.close(result_r)


def main():
    if sys.platform != "linux" or os.getpid() != 1 or not hasattr(os, "pidfd_open"):
        raise RuntimeError("requires isolated Linux container PID 1 with pidfd_open")
    run_scenario(False)
    run_scenario(True)
    print("SUPERVISED_START_PROBE_OK", flush=True)


if __name__ == "__main__":
    main()
