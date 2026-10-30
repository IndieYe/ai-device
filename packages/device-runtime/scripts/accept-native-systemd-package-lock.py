#!/usr/bin/env python3
"""Disposable Linux systemd probe of real cross-process package leases.

This does not dispatch jobs, start Device services or establish code identity.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from device_runtime import native_helper as helper

if (sys.platform != "linux" or os.geteuid() != 0
        or os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "systemd-package-lock-v1"
        or Path("/proc/1/comm").read_text().strip() != "systemd"):
    raise SystemExit("disposable Linux systemd root probe required")


def worker(registry, mode):
    with helper.package_lease(registry, exclusive=(mode == "exclusive")):
        (registry / "ready").write_text("ready")
        deadline = time.monotonic() + 30
        while not (registry / "release").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("release timeout")
            time.sleep(0.05)
    (registry / "done").write_text("done")


if len(sys.argv) == 4 and sys.argv[1] == "worker":
    worker(Path(sys.argv[2]), sys.argv[3])
    raise SystemExit(0)
if len(sys.argv) != 1:
    raise SystemExit("invalid arguments")

with tempfile.TemporaryDirectory(prefix="device-lock-probe-", dir="/run") as temporary:
    base = Path(temporary)
    for mode in ("shared", "exclusive"):
        registry = base / mode
        registry.mkdir()
        unit = f"device-lock-probe-{os.getpid()}-{mode}.service"
        subprocess.run([
            "/usr/bin/systemd-run", "--no-block", "--unit", unit,
            "--property=RemainAfterExit=yes",
            "--setenv=DEVICE_NATIVE_DISPOSABLE_PROBE=systemd-package-lock-v1",
            "--setenv=PYTHONPATH=/src/src", "/usr/bin/python3", str(Path(__file__)),
            "worker", str(registry), mode,
        ], check=True, capture_output=True, timeout=15)
        try:
            deadline = time.monotonic() + 20
            while not (registry / "ready").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{mode} worker did not become ready")
                time.sleep(0.05)
            try:
                with helper.package_lease(registry, exclusive=(mode == "shared")):
                    raise AssertionError(f"{mode} lock did not exclude opposing lease")
            except helper.HelperError as error:
                if "still executing" not in str(error):
                    raise
        finally:
            (registry / "release").touch()
        deadline = time.monotonic() + 20
        while not (registry / "done").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError(f"{mode} worker did not release")
            time.sleep(0.05)
        with helper.package_lease(registry, exclusive=(mode == "shared")):
            pass
        print(f"{mode}: opposing lease refused until normal release", flush=True)
print("systemd_package_lock_probe_passed")
