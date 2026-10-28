"""Check whether the real dispatch argv shape blocks, and whether the
invocation ID is available to the dispatcher at dispatch time.

Disposable systemd container only, inert fixtures. Reproduces the exact
property set used by _prepare_dispatch_locked, but with a harmless command.
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


def real_argv(unit, command):
    """Same flags as _prepare_dispatch_locked, minus the frozen python worker."""
    return [
        "/usr/bin/systemd-run",
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


def case_blocking():
    """Does the real flag set wait for ExecStart to finish?"""
    unit = f"inv3-block-{RUN}.service"
    worker = Path("/root/inv3-hold.sh")
    worker.write_text("#!/bin/sh\nsleep 6\nexit 0\n")
    worker.chmod(0o700)
    started = time.monotonic()
    result = subprocess.run(
        real_argv(unit, str(worker)),
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    elapsed = time.monotonic() - started
    stopped = time.monotonic()
    subprocess.run(["/usr/bin/systemctl", "stop", unit], capture_output=True, timeout=20)
    return {
        "elapsed_seconds": round(elapsed, 2),
        "returncode": result.returncode,
        "stdout": result.stdout.strip()[:200],
        "stderr": result.stderr.strip()[:300],
        "blocked_until_execstart_done": elapsed >= 5.5,
        "stop_elapsed": round(time.monotonic() - stopped, 2),
    }


def case_invocation_from_stderr():
    """Is the invocation id reported to the dispatcher?"""
    unit = f"inv3-id-{RUN}.service"
    result = subprocess.run(
        real_argv(unit, "/bin/true"),
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    show = subprocess.run(
        ["/usr/bin/systemctl", "show", unit, "--all", "--no-pager",
         "--property=InvocationID,LoadState,ActiveState,Result"],
        capture_output=True, text=True, timeout=20,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    fields = dict(
        line.partition("=")[::2] for line in show.stdout.splitlines() if "=" in line
    )
    reported = None
    for token in (result.stdout + " " + result.stderr).replace(";", " ").split():
        if len(token) == 32 and all(c in "0123456789abcdef" for c in token):
            reported = token
    return {
        "stderr": result.stderr.strip()[:300],
        "reported_in_output": reported,
        "show_invocation": fields.get("InvocationID"),
        "match": reported is not None and reported == fields.get("InvocationID"),
        "load": fields.get("LoadState"),
        "active": fields.get("ActiveState"),
    }


def case_no_block_alternative():
    """With --no-block, is the id still reported and returned immediately?"""
    unit = f"inv3-nb-{RUN}.service"
    worker = Path("/root/inv3-hold2.sh")
    worker.write_text("#!/bin/sh\nsleep 6\nexit 0\n")
    worker.chmod(0o700)
    argv = real_argv(unit, str(worker))
    argv.insert(1, "--no-block")
    started = time.monotonic()
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    elapsed = time.monotonic() - started
    shown = subprocess.run(
        ["/usr/bin/systemctl", "show", unit, "--all", "--no-pager",
         "--property=InvocationID,ActiveState,SubState,Job"],
        capture_output=True, text=True, timeout=20,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    fields = dict(
        line.partition("=")[::2] for line in shown.stdout.splitlines() if "=" in line
    )
    subprocess.run(["/usr/bin/systemctl", "stop", unit], capture_output=True, timeout=30)
    return {
        "elapsed_seconds": round(elapsed, 2),
        "returned_immediately": elapsed < 3.0,
        "stderr": result.stderr.strip()[:300],
        "show_invocation": fields.get("InvocationID"),
        "show_active": fields.get("ActiveState"),
        "show_job": fields.get("Job"),
    }


def main():
    guard()
    results = {
        "real_argv_blocks": case_blocking(),
        "invocation_from_dispatch_output": case_invocation_from_stderr(),
        "no_block_alternative": case_no_block_alternative(),
    }
    print("DISPATCH_RESULTS " + json.dumps(results, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("PROBE_FAILED " + type(exc).__name__ + ": " + str(exc), flush=True)
        raise SystemExit(1)
