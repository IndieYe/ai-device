"""Disposable listener test for the unwired per-recv credential fixture.

Not production authentication, and not proof of handler or invocation identity.
"""
import importlib.util
import os
import socket
import sys
import time
from pathlib import Path


def load_receiver():
    spec = importlib.util.spec_from_file_location("frame_fixture", "/fixture/frame.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_case(receiver, kind):
    path = f"/run/credential-listener-{kind}.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        listener.bind(path)
        listener.listen(1)
        listener.settimeout(5)
        read_ready, write_ready = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                listener.close()
                os.close(read_ready)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(path)
                    if kind == "nonroot":
                        os.setgroups([])
                        os.setgid(12345)
                        os.setuid(12345)
                    payload = {
                        "root": b"request\n",
                        "nonroot": b"request\n",
                        "oversize": b"x" * (receiver.MAX_FRAME + 1) + b"\n",
                        "trailing": b"one\ntwo\n",
                        "incomplete": b"partial",
                        "timeout": b"partial",
                        "slow": b"a",
                        "slow-deadline": b"a",
                    }[kind]
                    client.sendall(payload)
                    os.write(write_ready, b"x")
                    os.close(write_ready)
                    if kind == "timeout":
                        time.sleep(1)
                    elif kind in ("slow", "slow-deadline"):
                        time.sleep(0.15)
                        client.sendall(b"b")
                        time.sleep(0.15)
                        try:
                            client.sendall(b"c\n")
                        except BrokenPipeError:
                            if kind != "slow-deadline":
                                raise
                os._exit(0)
            except (OSError, ValueError):
                os._exit(2)
        os.close(write_ready)
        try:
            # The byte must be sent before accept; passcred must already be on.
            assert os.read(read_ready, 1) == b"x"
            with listener.accept()[0] as connection:
                assert connection.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == 1
                connection.settimeout(0.25 if kind in ("timeout", "slow") else 5)
                started = time.monotonic()
                try:
                    deadline = started + 0.25 if kind == "slow-deadline" else None
                    frame, sender = receiver.receive_root_frame(connection, deadline=deadline)
                except (receiver.Refused, TimeoutError) as exc:
                    if kind == "root":
                        raise AssertionError("root request unexpectedly refused") from exc
                    if kind in ("timeout", "slow-deadline"):
                        assert isinstance(exc, (TimeoutError, receiver.Refused))
                else:
                    if kind == "slow":
                        assert frame == b"abc\n" and sender == (pid, 0, 0)
                        assert time.monotonic() - started >= 0.25
                    else:
                        assert kind == "root" and frame == b"request\n" and sender == (pid, 0, 0)
            waited, status = os.waitpid(pid, 0)
            assert waited == pid and os.waitstatus_to_exitcode(status) == 0
        finally:
            os.close(read_ready)
    print(f"LISTENER_{kind.upper()}_OK", flush=True)


def main():
    if (sys.platform != "linux" or os.getpid() != 1 or os.geteuid() != 0
            or os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "request-listener-credentials-v1"):
        raise RuntimeError("disposable Linux root PID1 fixture required")
    assert Path("/fixture/frame.py").is_file()
    receiver = load_receiver()
    for kind in ("root", "nonroot", "oversize", "trailing", "incomplete", "timeout", "slow", "slow-deadline"):
        run_case(receiver, kind)
    print("ACCEPT_PREQUEUED_PASSCRED_ONLY_FIXTURE_NO_DISPATCH", flush=True)


if __name__ == "__main__":
    main()
