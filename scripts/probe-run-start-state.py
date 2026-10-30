"""Reproduce: run_start turns any unqueryable unit into "completed".

observe_job rewrites dispatch_unknown to job_completed_no_unit_evidence whenever
run_start evidence exists. But dispatch_unknown also covers "systemctl failed",
"output was unparseable", and "unit is loaded but inactive". Only the first is
attributable to a vanished unit. A reader cannot tell them apart afterwards.
"""
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/pkg")
from device_runtime import native_update_job as job  # noqa: E402

ROOT = Path("/root/runstart/manager")
PREPARED = {"metadata": {"Package": "pkg", "Version": "1.0", "Architecture": "amd64"}}
FAILED = "LoadState=loaded\nActiveState=failed\nSubState=failed\nResult=exit-code\nMainPID=0\nJob=\n"
DEAD = "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\nJob=\n"
GONE = "LoadState=not-found\nActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\nJob=\n"
BOOT = "11111111-2222-3333-4444-555555555555"


def build(name, run_start=True):
    updates = ROOT / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    stem = updates / f"5-probe--{name}"
    prepared = stem.with_suffix(".prepared.json")
    prepared.write_text(json.dumps(PREPARED))
    digest = job.helper._digest(prepared)
    stem.with_suffix(".approved.json").write_text(
        json.dumps({"prepared_sha256": digest, "approved_by_uid": 0})
    )
    intent_path = stem.with_suffix(".job-intent.json")
    intent_path.write_text(
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
                "runtime_snapshot_sha256": "b" * 64,
            }
        )
    )
    if run_start:
        stem.with_suffix(".job-run-start.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "state": "job_run_started",
                    "job_intent_sha256": job.helper._digest(intent_path),
                    "boot_id": BOOT,
                }
            )
        )
    return stem


def runner_oserror(*_a, **_k):
    raise OSError("systemctl missing")


def runner_garbage(*_a, **_k):
    return SimpleNamespace(stdout="this is not systemd output\n")


def runner_timeout(*_a, **_k):
    import subprocess

    raise subprocess.TimeoutExpired(cmd="systemctl", timeout=15)


def runner(stdout):
    return lambda *a, **k: SimpleNamespace(stdout=stdout)


def run(name, r, run_start=True):
    build(name, run_start)
    try:
        report = job.observe_job(
            SimpleNamespace(registry=ROOT), "probe", name, runner=r
        )
        return {
            "state": report["state"],
            "completion_basis": report.get("completion_basis"),
            "unit_observation": report["unit_observation"],
            "retry_allowed": report["retry_allowed"],
            "fence_retained": report["fence_retained"],
        }
    except job.JobError as exc:
        return {"outcome": "JobError", "detail": str(exc)}


def main():
    if os.geteuid() != 0:
        raise SystemExit("root required")
    ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(ROOT, 0o700)
    (ROOT / "updates").mkdir(exist_ok=True)
    os.chmod(ROOT / "updates", 0o700)

    cases = {
        "systemctl_unusable": runner_oserror,
        "systemctl_timeout": runner_timeout,
        "garbage_output": runner_garbage,
        "unit_loaded_inactive": runner(DEAD),
        "unit_not_found": runner(GONE),
        "unit_failed": runner(FAILED),
    }
    results = {n: run(n.replace("_", "-"), r) for n, r in cases.items()}
    # Same inputs without start evidence, for contrast.
    contrast = {
        n: run(n.replace("_", "-") + "-nors", r, run_start=False)
        for n, r in cases.items()
    }
    print(
        "WITH_RUN_START " + json.dumps(results, indent=2, sort_keys=True), flush=True
    )
    print("WITHOUT_RUN_START " + json.dumps(contrast, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    main()
