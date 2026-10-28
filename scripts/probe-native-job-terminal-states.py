"""Observe real systemd job terminal states with the current read-only parser.

Disposable systemd container only. Never dispatches a real package transaction:
the units here are inert fixtures. The point is to record what the current
parser reports after failure, cancellation and unit removal, and to prove it
never converts an unknown into retry permission.
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace


def guard():
    if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes":
        raise RuntimeError("disposable probe marker required")
    if os.geteuid() != 0:
        raise RuntimeError("disposable root required")
    if Path("/proc/1/comm").read_text().strip() != "systemd":
        raise RuntimeError("isolated systemd required")
    if Path("/opt/ai-device/instances").exists():
        raise RuntimeError("must not run against an installed instance")


def systemctl(*args):
    return subprocess.run(
        ["/usr/bin/systemctl", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout


def load_module():
    spec = importlib.util.spec_from_file_location(
        "device_runtime.native_update_job_probe", "/source/native_update_job.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_transaction(module, root, stem):
    """Create minimal but exactly-shaped evidence for the read-only parser."""
    updates = root / "updates"
    updates.mkdir(parents=True, mode=0o700)
    prepared = stem.with_suffix(".prepared.json")
    prepared.write_text(
        json.dumps({"metadata": {"Package": "fixture", "Version": "2", "Architecture": "amd64"}})
    )
    digest = module.helper._digest(prepared)
    stem.with_suffix(".approved.json").write_text(
        json.dumps({"prepared_sha256": digest, "approved_by_uid": 0})
    )
    unit = "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service"
    stem.with_suffix(".job-intent.json").write_text(
        json.dumps(
            {
                "unit": unit,
                "instance_id": "probe",
                "operation_id": stem.name.split("--", 1)[1],
                "prepared_sha256": digest,
                "runtime_snapshot_sha256": "snapshot-fixture",
            }
        )
    )
    return unit


def observe(module, root, operation="one"):
    return module.observe_job(SimpleNamespace(registry=root), "probe", operation)


def wait_for(module, root, predicate, timeout=20, operation="one"):
    deadline = time.monotonic() + timeout
    while True:
        report = observe(module, root, operation)
        if predicate(report):
            return report
        if time.monotonic() >= deadline:
            raise TimeoutError(json.dumps(report, sort_keys=True))
        time.sleep(0.1)


def assert_never_retry(report):
    assert report["retry_allowed"] is False, report
    assert report["fence_retained"] is True, report


def case_failed(module, root):
    stem = root / "updates" / "5-probe--one"
    unit = make_transaction(module, root, stem)
    subprocess.run(
        [
            "/usr/bin/systemd-run",
            "--unit",
            unit,
            "--property=RemainAfterExit=yes",
            "/bin/false",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    report = wait_for(
        module,
        stem.parent.parent,
        lambda value: value["unit_observation"]
        and value["unit_observation"]["ActiveState"] == "failed",
    )
    assert_never_retry(report)
    fields = report["unit_observation"]
    assert fields["Result"] == "exit-code", fields
    assert fields["MainPID"] == "0", fields
    print("REAL_SYSTEMD_FAILED_JOB_REPORTED_AS_REVIEW_REQUIRED", flush=True)
    return report


def case_cancelled(module, root):
    stem = root / "updates" / "7-probe--two"
    unit = make_transaction(module, root, stem)
    worker = root / "blocking-worker"
    worker.write_text("#!/bin/sh\nsleep 300\n")
    worker.chmod(0o700)
    subprocess.run(
        [
            "/usr/bin/systemd-run",
            "--unit",
            unit,
            "--property=RemainAfterExit=yes",
            "--",
            str(worker),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    running = wait_for(
        module,
        stem.parent.parent,
        lambda value: value["unit_observation"]
        and value["unit_observation"]["ActiveState"] == "active",
        operation="two",
    )
    assert_never_retry(running)
    assert running["state"] == "job_in_progress", running
    job_id = running["unit_observation"]["Job"]
    assert job_id not in ("", "0"), running
    systemctl("cancel", job_id)
    stopped = wait_for(
        module,
        stem.parent.parent,
        lambda value: value["unit_observation"]
        and value["unit_observation"]["ActiveState"] in ("failed", "inactive"),
        operation="two",
    )
    assert_never_retry(stopped)
    assert stopped["state"] == "job_failed_requires_review", stopped
    print("REAL_SYSTEMD_CANCELLED_JOB_BECOMES_REVIEW_REQUIRED", flush=True)
    return stopped


def case_unit_removed(module, root):
    stem = root / "updates" / "7-probe--three"
    unit = make_transaction(module, root, stem)
    subprocess.run(
        [
            "/usr/bin/systemd-run",
            "--unit",
            unit,
            "--property=RemainAfterExit=yes",
            "/bin/true",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    wait_for(
        module,
        stem.parent.parent,
        lambda value: value["unit_observation"]
        and value["unit_observation"]["ActiveState"] == "active",
        operation="three",
    )
    systemctl("reset-failed", unit)
    subprocess.run(
        ["/usr/bin/systemctl", "clean", unit], check=False, capture_output=True, timeout=30
    )
    subprocess.run(
        ["/usr/bin/systemctl", "stop", unit], check=False, capture_output=True, timeout=30
    )
    report = wait_for(
        module,
        stem.parent.parent,
        lambda value: value["unit_observation"]
        and value["unit_observation"]["LoadState"] == "not-found",
        operation="three",
    )
    assert_never_retry(report)
    assert report["state"] == "dispatch_unknown", report
    print("REAL_SYSTEMD_REMOVED_UNIT_RETURNS_TO_UNKNOWN", flush=True)
    return report


def main():
    guard()
    module = load_module()
    root = Path("/run/device-job-terminal-probe")
    root.mkdir(mode=0o700)
    failed = case_failed(module, root)
    cancelled = case_cancelled(module, root)
    removed = case_unit_removed(module, root)
    summary = {
        "failed": failed["state"],
        "cancelled": cancelled["state"],
        "removed": removed["state"],
        "retry_allowed": [
            failed["retry_allowed"],
            cancelled["retry_allowed"],
            removed["retry_allowed"],
        ],
    }
    print("TERMINAL_STATE_SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - probe must surface failures
        print("PROBE_FAILED " + type(exc).__name__ + ": " + str(exc), flush=True)
        sys.exit(1)
