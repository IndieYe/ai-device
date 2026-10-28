"""Observe real systemd job terminal states with the current read-only parser.

Disposable systemd container only. Every unit is an inert fixture: nothing here
dispatches a real package transaction. What is recorded is only what the current
parser reports, and that no report ever grants retry.

Cancellation note: `systemctl cancel` needs a queued job. A Type=simple start job
completes as soon as the process is forked, so the queue is always empty by the
time we look. A oneshot unit with RemainAfterExit keeps its start job queued for
the whole ExecStart, which is the only way to observe a real queued job.
"""
import hashlib
import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path("/pkg")
PROBE_ROOT = Path("/root/probe")


def guard():
    if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes":
        raise RuntimeError("disposable probe marker required")
    if os.geteuid() != 0:
        raise RuntimeError("disposable root required")
    if Path("/proc/1/comm").read_text().strip() != "systemd":
        raise RuntimeError("isolated systemd required")
    if Path("/opt/ai-device/instances").exists():
        raise RuntimeError("must not run against an installed instance")


def load_module():
    """Import as a package member so relative imports resolve to the bound source."""
    sys.path.insert(0, str(SOURCE))
    package = importlib.import_module("device_runtime")
    expected = (SOURCE / "device_runtime").resolve()
    if Path(package.__file__).resolve().parent != expected:
        raise RuntimeError("bound source must win the import")
    return importlib.import_module("device_runtime.native_update_job")


def systemd_run(unit, command):
    return subprocess.run(
        [
            "/usr/bin/systemd-run",
            "--no-block",
            "--unit", unit,
            "--property=Type=oneshot",
            "--property=RemainAfterExit=yes",
            "--", command,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def systemctl(*args, check=True):
    return subprocess.run(
        ["/usr/bin/systemctl", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
    )


def write_evidence(stem, module):
    """Build the exact evidence shape observe_job validates, in order."""
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
                "instance_id": INSTANCE,
                "operation_id": stem.name.split("--", 1)[1],
                "prepared_sha256": digest,
                "runtime_snapshot_sha256": "snapshot-fixture",
            }
        )
    )
    return unit


def new_stem(operation):
    directory = PROBE_ROOT / "updates"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    return directory / f"{len(INSTANCE)}-{INSTANCE}--{operation}{RUN}"


def observe(module, operation):
    return module.observe_job(SimpleNamespace(registry=PROBE_ROOT), INSTANCE, operation + RUN)


def wait_for(module, operation, predicate, timeout=25):
    deadline = time.monotonic() + timeout
    last = None
    while True:
        last = observe(module, operation)
        if predicate(last):
            return last
        if time.monotonic() >= deadline:
            raise TimeoutError(json.dumps(last, sort_keys=True, default=str))
        time.sleep(0.1)


def check_invariants(report, label):
    """The parser must never convert any observation into permission."""
    assert report["retry_allowed"] is False, (label, report)
    assert report["fence_retained"] is True, (label, report)


def unit_observation(report):
    return report["unit_observation"] or {}


def case_failed(module):
    stem = new_stem("one")
    unit = write_evidence(stem, module)
    # RemainAfterExit keeps the failed state inspectable after exit.
    systemd_run(unit, "/bin/false")
    report = wait_for(
        module, "one", lambda r: unit_observation(r).get("ActiveState") == "failed"
    )
    check_invariants(report, "failed")
    fields = unit_observation(report)
    assert fields["LoadState"] == "loaded", fields
    assert fields["MainPID"] == "0", fields
    assert fields["Job"] in ("", "0"), fields
    assert report["state"] == "job_failed_requires_review", report
    print("FAILED " + json.dumps(fields, sort_keys=True), flush=True)
    return report


def case_cancelled(module):
    stem = new_stem("two")
    unit = write_evidence(stem, module)
    worker = PROBE_ROOT / "blocking-worker.sh"
    worker.write_text("#!/bin/sh\nexec sleep 300\n")
    worker.chmod(0o700)
    # oneshot keeps the start job queued while ExecStart runs.
    systemd_run(unit, str(worker))
    running = wait_for(
        module, "two", lambda r: unit_observation(r).get("ActiveState") == "activating"
    )
    check_invariants(running, "cancelled-running")
    running_fields = unit_observation(running)
    job_id = running_fields["Job"]
    assert job_id not in ("", "0"), running_fields
    assert running["state"] == "job_in_progress", running
    print("QUEUED_BEFORE_CANCEL " + json.dumps(running_fields, sort_keys=True), flush=True)

    # A queued start job does NOT own the ExecStart process. Cancelling it clears
    # the job id while the unit keeps running, so the parser must keep reporting
    # in-progress. Treating cancel as terminal would be a safety bug.
    systemctl("cancel", job_id)
    after_cancel = wait_for(
        module, "two", lambda r: unit_observation(r).get("Job") in ("", "0")
    )
    check_invariants(after_cancel, "cancelled-queue")
    fields = unit_observation(after_cancel)
    assert fields["ActiveState"] == "activating", fields
    assert fields["MainPID"] != "0", fields
    assert after_cancel["state"] == "job_in_progress", after_cancel
    print("CANCELLED_QUEUE_STILL_RUNNING " + json.dumps(fields, sort_keys=True), flush=True)

    # Only an explicit stop produces a terminal state for the same unit.
    systemctl("stop", unit, check=False)
    stopped = wait_for(
        module,
        "two",
        lambda r: unit_observation(r).get("ActiveState") in ("failed", "inactive"),
    )
    check_invariants(stopped, "cancelled-stopped")
    stopped_fields = unit_observation(stopped)
    assert stopped_fields["MainPID"] == "0", stopped_fields
    assert stopped_fields["Job"] in ("", "0"), stopped_fields
    assert stopped["state"] in ("job_failed_requires_review", "dispatch_unknown"), stopped
    print("AFTER_STOP " + json.dumps(stopped_fields, sort_keys=True), flush=True)
    return {"cancel": after_cancel["state"], "stop": stopped["state"]}


def case_unit_removed(module):
    stem = new_stem("three")
    unit = write_evidence(stem, module)
    systemd_run(unit, "/bin/true")
    done = wait_for(
        module, "three", lambda r: unit_observation(r).get("ActiveState") == "active"
    )
    check_invariants(done, "removed-active")
    assert done["state"] == "job_in_progress", done
    assert unit_observation(done)["SubState"] == "exited", done

    systemctl("stop", unit, check=False)
    systemctl("reset-failed", unit, check=False)
    systemctl("clean", unit, "--what=all", check=False)
    report = wait_for(
        module, "three", lambda r: unit_observation(r).get("LoadState") == "not-found"
    )
    check_invariants(report, "removed")
    fields = unit_observation(report)
    assert report["state"] == "dispatch_unknown", report
    print("UNIT_REMOVED " + json.dumps(fields, sort_keys=True), flush=True)
    return report


def case_unreachable_systemctl(module):
    """A runner failure must also stay unknown, never retry."""
    stem = new_stem("four")
    write_evidence(stem, module)
    report = module.observe_job(
        SimpleNamespace(registry=PROBE_ROOT),
        INSTANCE,
        "four" + RUN,
        runner=lambda *a, **k: (_ for _ in ()).throw(OSError("no systemctl")),
    )
    check_invariants(report, "unreachable")
    assert report["state"] == "dispatch_unknown", report
    assert report["unit_observation"] is None, report
    print("UNREACHABLE_SYSTEMCTL_STAYS_UNKNOWN", flush=True)
    return report


INSTANCE = "probe"
# Unique per run: a leftover failed unit from a previous run must never be
# mistaken for this run's terminal state.
RUN = os.environ.get("PROBE_RUN_ID") or str(int(time.time()))


def main():
    guard()
    module = load_module()
    print("SOURCE " + module.__file__, flush=True)
    PROBE_ROOT.mkdir(mode=0o700, exist_ok=True)
    results = {
        "failed": case_failed(module)["state"],
        "cancelled": case_cancelled(module),
        "removed": case_unit_removed(module)["state"],
        "unreachable": case_unreachable_systemctl(module)["state"],
    }
    print("TERMINAL_STATE_SUMMARY " + json.dumps(results, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - probe must surface failures
        print("PROBE_FAILED " + type(exc).__name__ + ": " + str(exc), flush=True)
        sys.exit(1)
