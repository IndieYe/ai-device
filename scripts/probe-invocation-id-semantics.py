"""Check what InvocationID can and cannot prove for a package job unit.

Disposable systemd container only. All units are inert fixtures.

Questions answered here, one experiment per line of output:
  1. Does a re-run of the same unit name get a different InvocationID?
  2. Does a still-loaded-but-stopped unit keep its old InvocationID?
  3. Does `systemctl show` on a unit that never ran report an empty one?
  4. Does a *reenabled* unit after `reset-failed`+`clean` lose it entirely?
  5. Can a live process be observed with an unchanged InvocationID?
"""
import json
import os
import subprocess
import time
from pathlib import Path

PROBE_ROOT = Path("/root/inv-probe")
FIELDS = "LoadState,ActiveState,SubState,Result,MainPID,InvocationID,Job"
RUN = str(int(time.time()))


def guard():
    if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes":
        raise RuntimeError("disposable probe marker required")
    if os.geteuid() != 0:
        raise RuntimeError("disposable root required")
    if Path("/proc/1/comm").read_text().strip() != "systemd":
        raise RuntimeError("isolated systemd required")
    if Path("/opt/ai-device/instances").exists():
        raise RuntimeError("must not run against an installed instance")


def show(unit):
    out = subprocess.run(
        ["/usr/bin/systemctl", "show", unit, "--all", "--no-pager", "--property=" + FIELDS],
        capture_output=True, text=True, timeout=20,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    fields = {}
    for line in out.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key] = value
    return fields


def run_unit(unit, command):
    return subprocess.run(
        [
            "/usr/bin/systemd-run", "--no-block", "--unit", unit,
            "--property=Type=oneshot", "--property=RemainAfterExit=yes",
            "--", command,
        ],
        capture_output=True, text=True, timeout=30,
    )


def wait_state(unit, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while True:
        fields = show(unit)
        if predicate(fields):
            return fields
        if time.monotonic() >= deadline:
            raise TimeoutError(json.dumps(fields, sort_keys=True))
        time.sleep(0.1)


def main():
    guard()
    PROBE_ROOT.mkdir(mode=0o700, exist_ok=True)
    results = {}

    # 1+2. Same unit name executed twice, then a live third run.
    unit = f"inv-probe-rerun-{RUN}.service"
    run_unit(unit, "/bin/true")
    first = wait_state(unit, lambda f: f.get("ActiveState") == "active")
    second_run = run_unit(unit, "/bin/true")
    time.sleep(1.5)
    second = show(unit)
    results["rerun_same_unit_name"] = {
        "first_invocation": first.get("InvocationID"),
        "second_invocation": second.get("InvocationID"),
        "unit_was_already_loaded": True,
        "systemd_run_exit": second_run.returncode,
        "systemd_run_stderr": (second_run.stderr or "").strip()[:200],
        "distinct": first.get("InvocationID") != second.get("InvocationID"),
    }

    # 3. After stop: does the old InvocationID survive?
    subprocess.run(["/usr/bin/systemctl", "stop", unit], capture_output=True, timeout=20)
    stopped = show(unit)
    results["stopped_keeps_invocation"] = {
        "invocation": stopped.get("InvocationID"),
        "same_as_last": stopped.get("InvocationID") == second.get("InvocationID"),
        "activestate": stopped.get("ActiveState"),
    }

    # 4. Never-started unit name.
    never = f"inv-probe-never-{RUN}.service"
    results["never_started"] = {
        k: v for k, v in show(never).items() if k in ("LoadState", "ActiveState", "InvocationID")
    }

    # 5. Live process: InvocationID stable while running?
    live = f"inv-probe-live-{RUN}.service"
    worker = PROBE_ROOT / "hold.sh"
    worker.write_text("#!/bin/sh\nexec sleep 120\n")
    worker.chmod(0o700)
    run_unit(live, str(worker))
    running = wait_state(live, lambda f: f.get("ActiveState") == "activating"
                         and f.get("MainPID") not in ("", "0"))
    time.sleep(1)
    again = show(live)
    results["live_invocation_stable"] = {
        "first": running.get("InvocationID"),
        "second": again.get("InvocationID"),
        "same": running.get("InvocationID") == again.get("InvocationID"),
        "mainpid": again.get("MainPID"),
    }
    subprocess.run(["/usr/bin/systemctl", "stop", live], capture_output=True, timeout=20)

    # 6. clean removes the unit: does InvocationID vanish with it?
    subprocess.run(["/usr/bin/systemctl", "stop", unit], capture_output=True, timeout=20)
    subprocess.run(["/usr/bin/systemctl", "reset-failed", unit], capture_output=True, timeout=20)
    subprocess.run(["/usr/bin/systemctl", "clean", unit, "--what=all"], capture_output=True, timeout=20)
    gone = show(unit)
    results["after_clean"] = {
        "LoadState": gone.get("LoadState"),
        "ActiveState": gone.get("ActiveState"),
        "InvocationID": repr(gone.get("InvocationID")),
    }

    print("INVOCATION_RESULTS " + json.dumps(results, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("PROBE_FAILED " + type(exc).__name__ + ": " + str(exc), flush=True)
        raise SystemExit(1)
