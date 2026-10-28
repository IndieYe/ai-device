"""End-to-end check of run-start attribution against real source code.

Disposable systemd container only. Builds a job transaction fixture directly on
disk (no package manager involved), starts the real worker module, kills it
mid-apply, and then asks the real observe_job what it sees.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PKG = Path("/pkg")
REGISTRY = Path("/root/attr/manager")
INSTANCE = "probe"


def guard():
    if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes":
        raise RuntimeError("disposable probe marker required")
    if os.geteuid() != 0:
        raise RuntimeError("disposable root required")
    if Path("/proc/1/comm").read_text().strip() != "systemd":
        raise RuntimeError("isolated systemd required")
    if Path("/opt/ai-device/instances").exists():
        raise RuntimeError("must not run against an installed instance")


def load(path):
    out = subprocess.run(
        [sys.executable, "-c", f"import json,sys;sys.path.insert(0,'{PKG}');"
         f"from device_runtime import native_update_job as j;print('ok')"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(PKG)},
    )
    assert out.returncode == 0, out.stderr


def make_transaction(operation):
    updates = REGISTRY / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    os.chmod(REGISTRY, 0o700)
    os.chmod(updates, 0o700)
    stem = updates / f"{len(INSTANCE)}-{INSTANCE}--{operation}"
    prepared = stem.with_suffix(".prepared.json")
    prepared.write_text(json.dumps(
        {"metadata": {"Package": "fixture", "Version": "2", "Architecture": "amd64"}}
    ))
    import hashlib

    digest = hashlib.sha256(prepared.read_bytes()).hexdigest()
    stem.with_suffix(".approved.json").write_text(
        json.dumps({"prepared_sha256": digest, "approved_by_uid": 0})
    )
    snapshot = stem.with_suffix(".bootstrap")
    snapshot.mkdir(exist_ok=True)
    (snapshot / "snapshot.json").write_text("snapshot fixture")
    unit = "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service"
    intent = {
        "schema_version": 1,
        "state": "job_dispatch_intent",
        "close_protocol": "package-lease-v1",
        "unit": unit,
        "instance_id": INSTANCE,
        "operation_id": operation,
        "prepared_sha256": digest,
        "runtime_snapshot_sha256": hashlib.sha256(
            (snapshot / "snapshot.json").read_bytes()
        ).hexdigest(),
    }
    path = stem.with_suffix(".job-intent.json")
    path.write_text(json.dumps(intent))
    for item in (prepared, path, stem.with_suffix(".approved.json")):
        os.chmod(item, 0o600)
    return stem, hashlib.sha256(path.read_bytes()).hexdigest()


def observe(operation):
    code = f"""
import json, sys
sys.path.insert(0, {str(PKG)!r})
from types import SimpleNamespace
from device_runtime import native_update_job as job
report = job.observe_job(SimpleNamespace(registry={str(REGISTRY)!r}), {INSTANCE!r}, {operation!r})
print(json.dumps(report, sort_keys=True))
"""
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(PKG)},
    )
    if out.returncode != 0:
        raise RuntimeError("observe failed: " + out.stderr.strip()[-500:])
    return json.loads(out.stdout)


def case_killed_before_apply(operation):
    """Worker starts, records run-start, then dies before applying."""
    stem, digest = make_transaction(operation)
    # -I ignores PYTHONPATH, so drive main() through a launcher that sets sys.path.
    launcher = (
        f"import sys;sys.path.insert(0,{str(PKG)!r});"
        "from device_runtime import native_update_job as j;"
        f"sys.argv=['worker','--registry',{str(REGISTRY)!r},'--instance-id',"
        f"{INSTANCE!r},'--operation-id',{operation!r},'--job-intent-sha256',{digest!r}];"
        "j.main()"
    )
    worker = subprocess.Popen(
        [sys.executable, "-B", "-c", launcher],
        env={**os.environ},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    start = stem.with_suffix(".job-run-start.json")
    deadline = time.monotonic() + 20
    while not start.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    recorded = start.exists()
    worker.send_signal(signal.SIGKILL)
    _out, err = worker.communicate(timeout=20)
    time.sleep(0.3)
    before = observe(operation)
    apply_present = stem.with_suffix(".apply-intent.json").exists()
    return {
        "run_start_written": recorded,
        "apply_intent_written": apply_present,
        "state": before["state"],
        "retry_allowed": before["retry_allowed"],
        "fence_retained": before["fence_retained"],
        "worker_stderr": (err or "").strip()[-400:],
    }


def case_duplicate_start(operation):
    """A second worker for the same dispatch must be refused."""
    stem, digest = make_transaction(operation)
    code = f"""
import sys
sys.path.insert(0, {str(PKG)!r})
from device_runtime import native_update_job as job
job._record_run_start({str(REGISTRY)!r}, {INSTANCE!r}, {operation!r}, {digest!r})
job._record_run_start({str(REGISTRY)!r}, {INSTANCE!r}, {operation!r}, {digest!r})
"""
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(PKG)},
    )
    return {
        "exit": out.returncode,
        "refused": "duplicate start" in out.stderr,
        "stderr": out.stderr.strip()[-200:],
    }


def main():
    guard()
    results = {
        "killed_before_apply": case_killed_before_apply("alpha"),
        "duplicate_start": case_duplicate_start("beta"),
    }
    print("RUN_START_RESULTS " + json.dumps(results, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("PROBE_FAILED " + type(exc).__name__ + ": " + str(exc), flush=True)
        raise SystemExit(1)
