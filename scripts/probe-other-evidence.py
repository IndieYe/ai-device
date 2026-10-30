"""Reproduce: malformed JSON in other job evidence crashes observe_job.

The prepared fix covered prepared["metadata"][...]. Other carriers are read as
dicts too, without shape checks, so `.get()` on a list/str escapes as
AttributeError instead of JobError.

Disposable systemd container; root-owned registry so trust checks pass.
"""
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/pkg")
from device_runtime import native_update_job as job  # noqa: E402

ROOT = Path("/root/otherev/manager")
UNIT = "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\nJob=\n"
PREPARED = {"metadata": {"Package": "pkg", "Version": "1.0", "Architecture": "amd64"}}


def base(name):
    """A fully valid transaction, so only the varied carrier is malformed."""
    updates = ROOT / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    stem = updates / f"5-probe--{name}"
    prepared = stem.with_suffix(".prepared.json")
    prepared.write_text(json.dumps(PREPARED))
    digest = job.helper._digest(prepared)
    stem.with_suffix(".approved.json").write_text(
        json.dumps({"prepared_sha256": digest, "approved_by_uid": 0})
    )
    runtime = "b" * 64
    stem.with_suffix(".job-intent.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state": "job_dispatch_intent",
                "close_protocol": "package-lease-v1",
                "unit": "ai-device-package-"
                + hashlib.sha256(str(stem).encode()).hexdigest()
                + ".service",
                "instance_id": "probe",
                "operation_id": name,
                "prepared_sha256": digest,
                "runtime_snapshot_sha256": runtime,
            }
        )
    )
    apply_intent = {
        "prepared_sha256": digest,
        "runtime_snapshot_sha256": runtime,
        "target_package": {
            "name": "pkg",
            "version": "1.0",
            "architecture": "amd64",
        },
    }
    stem.with_suffix(".apply-intent.json").write_text(json.dumps(apply_intent))
    return stem, apply_intent


def run(name, mutate):
    stem, apply_intent = base(name)
    mutate(stem, apply_intent)
    try:
        report = job.observe_job(
            SimpleNamespace(registry=ROOT),
            "probe",
            name,
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

    def intent_list(stem, _apply):
        stem.with_suffix(".job-intent.json").write_text(json.dumps([1, 2]))

    def outcome_list(stem, _apply):
        stem.with_suffix(".outcome.json").write_text(json.dumps([1, 2]))

    def outcome_string(stem, _apply):
        stem.with_suffix(".outcome.json").write_text(json.dumps("text"))

    def sender_list(stem, apply_intent):
        stem.with_suffix(".outcome.json").write_text(
            json.dumps({"intent": apply_intent, "state": "unknown"})
        )
        stem.with_suffix(".sender.json").write_text(json.dumps([1, 2]))

    def outcome_list_with_sender(stem, apply_intent):
        stem.with_suffix(".outcome.json").write_text(json.dumps([1, 2]))
        stem.with_suffix(".sender.json").write_text(
            json.dumps(
                {
                    "intent": apply_intent,
                    "state": "sender_reaped",
                    "returncode": 0,
                }
            )
        )

    cases = {
        "intent_is_list": intent_list,
        "outcome_is_list": outcome_list,
        "outcome_is_string": outcome_string,
        "sender_is_list": sender_list,
        "outcome_list_with_sender": outcome_list_with_sender,
    }
    results = {n.replace("_", "-"): run(n.replace("_", "-"), f) for n, f in cases.items()}
    print("OTHER_EVIDENCE " + json.dumps(results, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    main()
