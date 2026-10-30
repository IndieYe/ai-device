"""Pin down exactly when InvocationID survives and when it is erased.

Disposable systemd container only, inert fixtures.

The first probe showed a stopped unit reports an empty InvocationID. That
decides the design question, so it needs to be isolated from the failed case.
"""
import json
import os
import subprocess
import time
from pathlib import Path

FIELDS = "LoadState,ActiveState,SubState,Result,MainPID,InvocationID,Job"
RUN = str(int(time.time()))


def guard():
    if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes":
        raise RuntimeError("disposable probe marker required")
    if os.geteuid() != 0:
        raise RuntimeError("disposable root required")
    if Path("/proc/1/comm").read_text().strip() != "systemd":
        raise RuntimeError("isolated systemd required")


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


def snapshot(label, fields):
    return {
        "label": label,
        "LoadState": fields.get("LoadState"),
        "ActiveState": fields.get("ActiveState"),
        "SubState": fields.get("SubState"),
        "Result": fields.get("Result"),
        "MainPID": fields.get("MainPID"),
        "InvocationID": fields.get("InvocationID"),
        "invocation_present": bool(fields.get("InvocationID")),
    }


def case_failed_then_stopped():
    unit = f"inv2-failed-{RUN}.service"
    run_unit(unit, "/bin/false")
    failed = wait_state(unit, lambda f: f.get("ActiveState") == "failed")
    before = snapshot("failed_before_stop", failed)
    subprocess.run(["/usr/bin/systemctl", "stop", unit], capture_output=True, timeout=20)
    after = snapshot("failed_after_stop", show(unit))
    return {"before_stop": before, "after_stop": after,
            "survives_stop": before["InvocationID"] == after["InvocationID"]}


def case_explicit_stop_of_success():
    unit = f"inv2-success-{RUN}.service"
    run_unit(unit, "/bin/true")
    active = wait_state(unit, lambda f: f.get("ActiveState") == "active")
    before = snapshot("active_exited_before_stop", active)
    subprocess.run(["/usr/bin/systemctl", "stop", unit], capture_output=True, timeout=20)
    after = snapshot("inactive_after_stop", show(unit))
    return {"before_stop": before, "after_stop": after,
            "survives_stop": before["InvocationID"] == after["InvocationID"]}


def case_restart_same_name():
    """Can the same unit name run a second time at all?"""
    unit = f"inv2-reuse-{RUN}.service"
    first = run_unit(unit, "/bin/true")
    first_state = wait_state(unit, lambda f: f.get("ActiveState") == "active")
    first_inv = first_state.get("InvocationID")
    subprocess.run(["/usr/bin/systemctl", "stop", unit], capture_output=True, timeout=20)
    subprocess.run(["/usr/bin/systemctl", "reset-failed", unit], capture_output=True, timeout=20)
    second = run_unit(unit, "/bin/true")
    time.sleep(1.0)
    second_state = show(unit)
    return {
        "first_invocation": first_inv,
        "first_run_exit": first.returncode,
        "second_run_exit": second.returncode,
        "second_run_stderr": (second.stderr or "").strip()[:200],
        "second_invocation": second_state.get("InvocationID"),
        "second_load": second_state.get("LoadState"),
        "reused": second.returncode == 0
        and second_state.get("InvocationID") not in (None, "", first_inv),
    }


def case_clean_then_rerun():
    """After clean the unit is gone; does a rerun get a fresh invocation?"""
    unit = f"inv2-clean-{RUN}.service"
    run_unit(unit, "/bin/true")
    first_state = wait_state(unit, lambda f: f.get("ActiveState") == "active")
    first_inv = first_state.get("InvocationID")
    subprocess.run(["/usr/bin/systemctl", "stop", unit], capture_output=True, timeout=20)
    subprocess.run(["/usr/bin/systemctl", "reset-failed", unit], capture_output=True, timeout=20)
    subprocess.run(["/usr/bin/systemctl", "clean", unit, "--what=all"],
                   capture_output=True, timeout=20)
    gone = snapshot("after_clean", show(unit))
    rerun = run_unit(unit, "/bin/true")
    time.sleep(1.0)
    again = show(unit)
    return {
        "first_invocation": first_inv,
        "after_clean": gone,
        "rerun_exit": rerun.returncode,
        "rerun_stderr": (rerun.stderr or "").strip()[:200],
        "rerun_invocation": again.get("InvocationID"),
        "rerun_load": again.get("LoadState"),
        "new_generation": bool(again.get("InvocationID"))
        and again.get("InvocationID") != first_inv,
    }


def main():
    guard()
    results = {
        "failed_then_stopped": case_failed_then_stopped(),
        "success_then_stopped": case_explicit_stop_of_success(),
        "reuse_same_name": case_restart_same_name(),
        "clean_then_rerun": case_clean_then_rerun(),
    }
    print("INVOCATION_RESULTS_2 " + json.dumps(results, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("PROBE_FAILED " + type(exc).__name__ + ": " + str(exc), flush=True)
        raise SystemExit(1)
