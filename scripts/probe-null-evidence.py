"""Does a JSON `null` evidence file get confused with a missing one?

Disposable systemd container, root-owned registry so trust checks pass.
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/pkg")
from device_runtime import native_update_job as job  # noqa: E402

ROOT = Path("/root/nulle/manager")
UNIT = "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\nJob=\n"


def build(name, null_files):
    updates = ROOT / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    stem = updates / f"5-probe--{name}"
    prepared = stem.with_suffix(".prepared.json")
    prepared.write_text(
        json.dumps({"metadata": {"Package": "f", "Version": "2", "Architecture": "amd64"}})
    )
    digest = job.helper._digest(prepared)
    stem.with_suffix(".approved.json").write_text(
        json.dumps({"prepared_sha256": digest, "approved_by_uid": 0})
    )
    stem.with_suffix(".job-intent.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state": "job_dispatch_intent",
                "close_protocol": "package-lease-v1",
                "unit": "ai-device-package-"
                + __import__("hashlib").sha256(str(stem).encode()).hexdigest()
                + ".service",
                "instance_id": "probe",
                "operation_id": name,
                "prepared_sha256": digest,
                "runtime_snapshot_sha256": "b" * 64,
            }
        )
    )
    for suffix in null_files:
        stem.with_suffix(suffix).write_text("null")
    return stem


def observe(name):
    return job.observe_job(
        SimpleNamespace(registry=ROOT), "probe", name,
        runner=lambda *a, **k: SimpleNamespace(stdout=UNIT),
    )


def case_absent(name):
    build(name, [])
    report = observe(name)
    return {"state": report["state"], "apply_intent_present": report.get("apply_intent_present")}


def case_null_apply_intent(name):
    build(name, [".apply-intent.json"])
    try:
        report = observe(name)
        return {
            "accepted": True,
            "state": report["state"],
            "apply_intent_present": report.get("apply_intent_present"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"accepted": False, "error": type(exc).__name__, "detail": str(exc)}


def case_null_sender_orphan(name):
    """A null sender with no apply intent: orphan check should catch, or not."""
    build(name, [".sender.json"])
    try:
        report = observe(name)
        return {"accepted": True, "state": report["state"]}
    except Exception as exc:  # noqa: BLE001
        return {"accepted": False, "error": type(exc).__name__, "detail": str(exc)}


def main():
    if os.geteuid() != 0:
        raise SystemExit("root required")
    ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(ROOT, 0o700)
    (ROOT / "updates").mkdir(exist_ok=True)
    os.chmod(ROOT / "updates", 0o700)
    results = {
        "no_apply_intent": case_absent("absent"),
        "null_apply_intent": case_null_apply_intent("nullapply"),
        "null_sender_no_apply": case_null_sender_orphan("nullsender"),
    }
    print("NULL_EVIDENCE_RESULTS " + json.dumps(results, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    main()
