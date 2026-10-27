"""Disposable systemd only: verify real helper query output, not authorization."""
import os
import subprocess
import time
from pathlib import Path

from device_runtime import native_helper as helper

if (os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "helper-observation-v1"
        or os.geteuid() != 0 or Path("/proc/1/comm").read_text().strip() != "systemd"):
    raise SystemExit("disposable systemd administrator required")
if Path(helper.__file__).resolve() != Path("/device-src/device_runtime/native_helper.py"):
    raise SystemExit("unexpected helper source")
unit = "device-observation-fixture.service"
subprocess.run(["/usr/bin/systemd-run", "--unit", unit, "--property=RemainAfterExit=yes", "/bin/true"],
               check=True, capture_output=True, timeout=15)
deadline = time.monotonic() + 10
while True:
    fields = helper.Systemd().query(unit)
    if fields["ActiveState"] == "active" and fields["SubState"] == "exited":
        break
    if time.monotonic() >= deadline:
        raise RuntimeError("fixture unit did not reach terminal state")
    time.sleep(0.05)
assert set(fields) == {"ActiveState", "SubState", "MainPID", "Job", "User", "Group", "FragmentPath", "ExecStart"}
assert fields["MainPID"] == "0" and fields["Job"] in ("", "0")
try:
    helper.Systemd().query("device-observation-missing.service")
except helper.HelperError as exc:
    assert "incomplete systemd unit observation" in str(exc)
else:
    raise AssertionError("missing unit accepted")
print("REAL_SYSTEMD_HELPER_OBSERVATION_SOURCE_OK", flush=True)
