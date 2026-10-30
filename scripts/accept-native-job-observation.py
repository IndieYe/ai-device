"""Disposable systemd fixture for the current job observation parser."""
import hashlib
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from device_runtime import native_helper as helper


def main():
    if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes" or os.geteuid() != 0:
        raise RuntimeError("disposable administrator context required")
    if Path("/proc/1/comm").read_text().strip() != "systemd":
        raise RuntimeError("isolated systemd required")
    spec = importlib.util.spec_from_file_location(
        "device_runtime.native_update_job_fixture", "/source/native_update_job.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = Path("/run/device-job-observe-fixture")
    updates = registry / "updates"
    updates.mkdir(parents=True, mode=0o700)
    stem = updates / "5-probe--one"
    prepared = stem.with_suffix(".prepared.json")
    prepared.write_text(json.dumps({"metadata": {"Package": "fixture", "Version": "2", "Architecture": "amd64"}}))
    digest = helper._digest(prepared)
    stem.with_suffix(".approved.json").write_text(json.dumps({"prepared_sha256": digest, "approved_by_uid": 0}))
    unit = "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service"
    stem.with_suffix(".job-intent.json").write_text(json.dumps({
        "unit": unit, "instance_id": "probe", "operation_id": "one", "prepared_sha256": digest,
    }))
    subprocess.run(["/usr/bin/systemd-run", "--unit", unit, "--property=RemainAfterExit=yes", "/bin/true"],
                   check=True, capture_output=True, timeout=15)
    manager = SimpleNamespace(registry=registry)
    deadline = time.monotonic() + 10
    while True:
        observed = module.observe_job(manager, "probe", "one")
        fields = observed["unit_observation"]
        if fields and fields["ActiveState"] == "active" and fields["SubState"] == "exited":
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("fixture job not observed")
        time.sleep(0.05)
    assert fields["MainPID"] == "0" and fields["Job"] in ("", "0")
    assert observed["retry_allowed"] is False and observed["fence_retained"] is True
    assert observed["apply_intent_present"] is False
    print("REAL_SYSTEMD_JOB_OBSERVATION_PARSER_OK", flush=True)


if __name__ == "__main__":
    main()
