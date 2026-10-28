"""Confirm the fixed dispatch argv returns immediately while the unit runs.

Disposable systemd container only. Uses the real flag set produced by
_prepare_dispatch_locked after the --no-block fix, with a harmless command.
"""
import json
import os
import subprocess
import time
from pathlib import Path

RUN = str(int(time.time()))


def guard():
    if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes":
        raise RuntimeError("disposable probe marker required")
    if os.geteuid() != 0:
        raise RuntimeError("disposable root required")
    if Path("/proc/1/comm").read_text().strip() != "systemd":
        raise RuntimeError("isolated systemd required")


def argv_for(unit, command):
    """Mirror the fixed _prepare_dispatch_locked argv order exactly."""
    return [
        "/usr/bin/systemd-run",
        "--no-block",
        "--unit=" + unit,
        "--service-type=oneshot",
        "--property=Restart=no",
        "--property=RemainAfterExit=yes",
        "--property=TimeoutStartSec=infinity",
        "--property=TimeoutStopSec=infinity",
        "--property=KillMode=process",
        "--property=SendSIGKILL=no",
        "--property=UMask=0077",
        "--",
        command,
    ]


def show(unit):
    out = subprocess.run(
        ["/usr/bin/systemctl", "show", unit, "--all", "--no-pager",
         "--property=LoadState,ActiveState,SubState,MainPID,InvocationID,Job"],
        capture_output=True, text=True, timeout=20,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    return dict(
        line.partition("=")[::2] for line in out.stdout.splitlines() if "=" in line
    )


def case_long_unit():
    unit = f"fix-long-{RUN}.service"
    worker = Path("/root/fix-hold.sh")
    worker.write_text("#!/bin/sh\nsleep 8\nexit 0\n")
    worker.chmod(0o700)
    started = time.monotonic()
    result = subprocess.run(
        argv_for(unit, str(worker)),
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    elapsed = time.monotonic() - started
    during = show(unit)
    return {
        "dispatch_elapsed_seconds": round(elapsed, 2),
        "returned_before_execstart_finished": elapsed < 3.0,
        "returncode": result.returncode,
        "stderr": result.stderr.strip()[:200],
        "state_while_running": {
            k: during.get(k) for k in ("LoadState", "ActiveState", "SubState", "MainPID")
        },
        "invocation_visible_at_dispatch": bool(during.get("InvocationID")),
    }


def case_accept_semantics():
    """Does a failing unit still report accepted at dispatch time?"""
    unit = f"fix-fail-{RUN}.service"
    started = time.monotonic()
    result = subprocess.run(
        argv_for(unit, "/bin/false"),
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    elapsed = time.monotonic() - started
    time.sleep(2.0)
    after = show(unit)
    return {
        "dispatch_elapsed_seconds": round(elapsed, 2),
        "dispatch_returncode": result.returncode,
        "state_after_execution": {
            k: after.get(k) for k in ("LoadState", "ActiveState", "Result", "MainPID")
        },
    }


def main():
    guard()
    results = {
        "long_running_unit": case_long_unit(),
        "failing_unit": case_accept_semantics(),
    }
    print("DISPATCH_FIX_RESULTS " + json.dumps(results, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("PROBE_FAILED " + type(exc).__name__ + ": " + str(exc), flush=True)
        raise SystemExit(1)
