#!/usr/bin/env python3
"""Disposable Linux only: exercise real Unix-socket peer pidfd and exit detection."""
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from device_runtime import native_helper as helper

if sys.platform != "linux" or os.geteuid() != 0 or os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "peer-pidfd-v1":
    raise SystemExit("disposable Linux root probe required")

with tempfile.TemporaryDirectory(prefix="device-peer-") as directory:
    endpoint = str(Path(directory) / "helper.sock")
    code = """import socket,sys
s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.listen(1)
print('ready',flush=True)
c,_=s.accept(); c.recv(1)
"""
    child = subprocess.Popen([sys.executable, "-c", code, endpoint], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with socket.socket(socket.AF_UNIX) as conn:
            conn.connect(endpoint)
            try:
                with helper._socket_peer_process(conn) as identity:
                    assert identity["pid"] == child.pid and identity["uid"] == 0
                    assert not os.get_inheritable(identity["pidfd"])
                    conn.sendall(b"x")
                    assert child.wait(timeout=5) == 0
            except helper.HelperError as error:
                assert "exited during observation" in str(error), error
            else:
                raise AssertionError("peer exit was not detected")
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            # Normal EOF lets the disposable child exit without signals.
            with socket.socket(socket.AF_UNIX) as conn:
                conn.connect(endpoint)
            child.wait(timeout=5)
print("peer_pidfd_bound_and_exit_refused")
