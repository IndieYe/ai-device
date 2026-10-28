"""Diagnose why _record_run_start does not leave a file."""
import hashlib
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/pkg")
from device_runtime import native_update_job as job  # noqa: E402

root = Path("/root/dbg/manager")
updates = root / "updates"
updates.mkdir(parents=True, exist_ok=True)
stem = updates / "5-probe--gamma"

prepared = stem.with_suffix(".prepared.json")
prepared.write_text(
    json.dumps({"metadata": {"Package": "fixture", "Version": "2", "Architecture": "amd64"}})
)
digest = hashlib.sha256(prepared.read_bytes()).hexdigest()
stem.with_suffix(".approved.json").write_text(
    json.dumps({"prepared_sha256": digest, "approved_by_uid": 0})
)
snap = stem.with_suffix(".bootstrap")
snap.mkdir(exist_ok=True)
(snap / "snapshot.json").write_text("snapshot fixture")
unit = "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service"
intent = {
    "schema_version": 1,
    "state": "job_dispatch_intent",
    "close_protocol": "package-lease-v1",
    "unit": unit,
    "instance_id": "probe",
    "operation_id": "gamma",
    "prepared_sha256": digest,
    "runtime_snapshot_sha256": hashlib.sha256(
        (snap / "snapshot.json").read_bytes()
    ).hexdigest(),
}
p = stem.with_suffix(".job-intent.json")
p.write_text(json.dumps(intent))
intent_digest = hashlib.sha256(p.read_bytes()).hexdigest()
print("intent_digest", intent_digest)

print("trusted read:", job.helper._digest(p) == intent_digest)

try:
    value = job._record_run_start(str(root), "probe", "gamma", intent_digest)
    print("RECORDED", value)
except Exception as exc:  # noqa: BLE001
    print("FAILED", type(exc).__name__, exc)
    traceback.print_exc()

target = stem.with_suffix(".job-run-start.json")
print("exists:", target.exists())
