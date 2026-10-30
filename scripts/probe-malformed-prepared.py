"""Reproduce: malformed-but-valid-JSON prepared evidence crashes observe_job.

Disposable systemd container; root-owned registry so trust checks pass.
"""
import json
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/pkg")
from device_runtime import native_update_job as job  # noqa: E402

ROOT = Path("/root/malformed/manager")
UNIT = "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\nJob=\n"


def build(name, prepared_value):
    updates = ROOT / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    stem = updates / f"5-probe--{name}"
    prepared = stem.with_suffix(".prepared.json")
    prepared.write_text(json.dumps(prepared_value))
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
    # apply-intent present forces the metadata dereference path.
    stem.with_suffix(".apply-intent.json").write_text(json.dumps({"whatever": True}))
    return stem


def run(name, prepared_value):
    build(name, prepared_value)
    try:
        report = job.observe_job(
            SimpleNamespace(registry=ROOT), "probe", name,
            runner=lambda *a, **k: SimpleNamespace(stdout=UNIT),
        )
        return {"outcome": "accepted", "state": report["state"]}
    except job.JobError as exc:
        return {"outcome": "JobError", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - this is the point
        return {
            "outcome": type(exc).__name__,
            "detail": str(exc),
            "trace_tail": traceback.format_exc().strip().splitlines()[-3:],
        }


def main():
    if os.geteuid() != 0:
        raise SystemExit("root required")
    ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(ROOT, 0o700)
    (ROOT / "updates").mkdir(exist_ok=True)
    os.chmod(ROOT / "updates", 0o700)
    cases = {
        "no_metadata": {"foo": 1},
        "metadata_not_object": {"metadata": "string"},
        "metadata_missing_keys": {"metadata": {"Package": "f"}},
        "metadata_null": {"metadata": None},
        "prepared_is_list": [1, 2, 3],
    }
    results = {name: run(name.replace("_", "-"), value) for name, value in cases.items()}
    print("MALFORMED_RESULTS " + json.dumps(results, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    main()
