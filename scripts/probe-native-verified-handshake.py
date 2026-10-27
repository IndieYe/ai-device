"""Disposable-container verified candidate handshake; never authorizes dispatch."""
import importlib.util
import os
import select
import socket
import sys
import types
from pathlib import Path

from device_runtime import native_bootstrap as bootstrap
from device_runtime import native_helper as helper

spec = importlib.util.spec_from_file_location("strict_record", "/scripts/probe-native-strict-record.py")
strict = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strict)


def main():
    if sys.platform != "linux" or os.geteuid() != 0 or os.getpid() != 1:
        raise RuntimeError("disposable Linux container PID 1 required")
    registry = Path("/var/lib/verified-handshake")
    registry.mkdir(mode=0o700)
    (registry / "bootstrap-candidates").mkdir(mode=0o700)
    runtime = Path("/opt/ai-device/bootstrap/0.2.2-native1/runtime")
    published = bootstrap.publish_managed_helper_candidate(registry, runtime, "candidate")
    contents = bootstrap._verified_helper_bytes(
        registry / "bootstrap-candidates", "candidate", published["snapshot_sha256"]
    )
    path, original = contents["native_helper"]
    path.write_bytes(b"raise RuntimeError('mutable candidate path was reloaded')\n")
    assert path.read_bytes() != original
    receiver, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    receiver.settimeout(5)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    gate_r, gate_w = os.pipe()
    child = None
    pidfd = None
    try:
        with helper._daemon_lease(registry):
            child = os.fork()
            if child == 0:
                try:
                    receiver.close()
                    os.close(gate_w)
                    challenge = sender.recv(32)
                    if len(challenge) != 32:
                        os._exit(2)
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
                    candidate = sys.modules["device_runtime.native_helper"]
                    if not callable(candidate._valid_sender_completion):
                        os._exit(3)
                    sender.sendall(b"READY:" + challenge)
                    if os.read(gate_r, 1) != b"X":
                        os._exit(4)
                    os._exit(0)
                except (OSError, RuntimeError, ValueError, SyntaxError):
                    os._exit(1)
            sender.close()
            os.close(gate_r)
            pidfd = os.pidfd_open(child)
            poller = select.poll()
            poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
            challenge = os.urandom(32)
            receiver.sendall(challenge)
            assert not poller.poll(0)
            assert strict.receive_record(receiver, child, 0) == b"READY:" + challenge
            assert not poller.poll(0)
            try:
                with helper._daemon_lease(registry):
                    raise AssertionError("daemon lease unexpectedly available")
            except helper.HelperError as exc:
                if "already active" not in str(exc):
                    raise
            os.write(gate_w, b"X")
            _, status = os.waitpid(child, 0)
            child = None
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
            assert poller.poll(0)
        with helper._daemon_lease(registry):
            pass
        print("VERIFIED_CANDIDATE_PIDFD_HANDSHAKE_FIXTURE_OK", flush=True)
    finally:
        receiver.close()
        os.close(gate_w)
        if pidfd is not None:
            os.close(pidfd)
        if child is not None:
            os.waitpid(child, 0)


if __name__ == "__main__":
    main()
