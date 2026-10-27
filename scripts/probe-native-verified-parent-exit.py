"""Disposable-container experiment, not a production supervisor or entry point."""
import os
import pathlib
import select
import socket
import struct
import sys
import threading
import time
import types

from device_runtime import native_bootstrap as bootstrap
from device_runtime import native_helper as helper


def read_line(fd):
    data = bytearray()
    deadline = time.monotonic() + 30
    while not data.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError("probe pipe timed out")
        part = os.read(fd, 1)
        if not part:
            raise RuntimeError("probe pipe closed")
        data.extend(part)
        if len(data) > 40:
            raise RuntimeError("probe pipe record too large")
    return data.decode().strip()


def run():
    if sys.platform != "linux" or os.geteuid() != 0 or os.getpid() != 1:
        raise RuntimeError("disposable container PID 1 and root required")
    registry = pathlib.Path("/var/lib/device-parent-probe")
    endpoint = pathlib.Path("/run/device-parent-probe/helper.sock")
    source = pathlib.Path("/opt/ai-device/bootstrap/0.2.2-native1/runtime")
    published = bootstrap.publish_managed_helper_candidate(registry, source, "candidate")
    contents = bootstrap._verified_helper_bytes(
        registry / "bootstrap-candidates", "candidate", published["snapshot_sha256"]
    )
    path, original = contents["native_helper"]
    path.write_bytes(b"raise RuntimeError('candidate path reread')\n")
    assert path.read_bytes() != original
    ready_r, ready_w = os.pipe()
    gate_r, gate_w = os.pipe()
    supervisor = os.fork()
    if supervisor == 0:
        try:
            os.close(ready_r)
            os.close(gate_w)
            supervisor_fd = os.pidfd_open(os.getpid())
            with helper._daemon_lease(registry):
                worker = os.fork()
                if worker == 0:
                    os.close(gate_r)
                    for name in ("__init__", "management_lock", "native_helper"):
                        module_path, data = contents[name]
                        qualified = "device_runtime" if name == "__init__" else "device_runtime." + name
                        module = types.ModuleType(qualified)
                        module.__file__ = str(module_path)
                        module.__package__ = "device_runtime"
                        if name == "__init__":
                            module.__path__ = [str(module_path.parent)]
                        sys.modules[qualified] = module
                        exec(compile(data, str(module_path), "exec"), module.__dict__)  # noqa: S102

                    def watchdog():
                        poller = select.poll()
                        poller.register(supervisor_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
                        poller.poll()
                        os._exit(0)

                    threading.Thread(target=watchdog, daemon=True).start()
                    os.write(ready_w, f"{os.getpid()}\n".encode())
                    os.close(ready_w)
                    # Deliberately bypasses production serve lease acquisition.
                    # No production claim: this experiment cannot authorize dispatch.
                    sys.modules["device_runtime.native_helper"]._serve(registry, endpoint, group=0)
                    os._exit(4)
                os.close(ready_w)
                if os.read(gate_r, 1) != b"X":
                    os._exit(5)
                os._exit(0)
        except (OSError, RuntimeError, ValueError, AssertionError, SyntaxError):
            os._exit(1)
    os.close(ready_w)
    os.close(gate_r)
    worker_fd = None
    try:
        worker_pid = int(read_line(ready_r))
        worker_fd = os.pidfd_open(worker_pid)
        poller = select.poll()
        poller.register(worker_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
        deadline = time.monotonic() + 30
        while not endpoint.exists() and not poller.poll(0) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert endpoint.exists() and not poller.poll(0)
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            client.connect(str(endpoint))
            with helper._socket_peer_process(client) as peer:
                assert peer["pid"] == worker_pid
                client.sendall(b"{}\n")
                response, controls, flags, _ = client.recvmsg(1024, socket.CMSG_SPACE(struct.calcsize("3i")))
                assert not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                assert b"helper_request_refused" in response and response.endswith(b"\n")
                credentials = [data for level, kind, data in controls
                               if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
                assert len(credentials) == 1 and len(credentials[0]) == struct.calcsize("3i")
                sender_pid, sender_uid, _sender_gid = struct.unpack("3i", credentials[0])
                assert sender_pid == worker_pid and sender_uid == 0
                print("VERIFIED_HELPER_REFUSAL_RESPONSE_SENDER_MATCHES_FIXTURE_CHILD", flush=True)
        try:
            with helper._daemon_lease(registry):
                raise AssertionError("worker did not hold inherited lease")
        except helper.HelperError as exc:
            if "already active" not in str(exc):
                raise
        os.write(gate_w, b"X")
        _, status = os.waitpid(supervisor, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        assert poller.poll(5000), "worker did not observe parent exit"
        with helper._daemon_lease(registry):
            pass
        assert endpoint.exists(), "unexpected automatic stale socket deletion"
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(2)
            try:
                client.connect(str(endpoint))
            except ConnectionRefusedError:
                pass
            else:
                raise AssertionError("stale endpoint unexpectedly accepted connection")
        print("VERIFIED_PARENT_EXIT_PIDFD_FIXTURE_OK", flush=True)
    finally:
        os.close(ready_r)
        os.close(gate_w)
        if worker_fd is not None:
            os.close(worker_fd)


if __name__ == "__main__":
    run()
