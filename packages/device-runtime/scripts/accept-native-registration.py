"""Registration interruption recovery in a disposable installed systemd instance."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from device_runtime import native_helper as nh

if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes" or os.geteuid() != 0:
    raise SystemExit("disposable administrator context required")
registry = Path("/var/lib/ai-device/manager")
units = Path("/etc/systemd/system")
root = Path("/opt/ai-device/instances/recovery")
subprocess.run(
    [
        "runuser",
        "-u",
        "nativeprobe",
        "--",
        "ai-device-native-install",
        "--root",
        str(root),
        "--instance-id",
        "recovery-probe",
        "--server-url",
        "http://127.0.0.1:9",
    ],
    check=True,
)
kwargs = {
    "instance_id": "recovery-probe",
    "root": root,
    "username": "nativeprobe",
    "environment_file": Path("/opt/ai-device/fixture.env"),
}
original_save = nh._save_new


def interrupt(path, value):
    original_save(path, value)
    if path == root / nh.MARKER:
        raise OSError("injected interruption after marker publication")


with patch.object(nh, "_save_new", side_effect=interrupt):
    try:
        nh.register(registry, units, **kwargs)
    except OSError as exc:
        assert "injected" in str(exc)
    else:
        raise AssertionError("expected interruption")
unit = units / "ai-device-client-recovery-probe.service"
record = registry / "recovery-probe.json"
assert (root / nh.MARKER).exists() and not unit.exists() and not record.exists()
result = nh.register(registry, units, **kwargs, recover=True)
assert result["started"] is False and result["enabled"] is False
saved = record.read_bytes()
# Verify public administrator CLI, not only direct function invocation.
subprocess.run(
    [
        "ai-device-native-helper",
        "recover-registration",
        "--registry",
        str(registry),
        "--unit-dir",
        str(units),
        "--root",
        str(root),
        "--instance-id",
        "recovery-probe",
        "--username",
        "nativeprobe",
        "--environment-file",
        "/opt/ai-device/fixture.env",
    ],
    check=True,
)
assert record.read_bytes() == saved
subprocess.run(["systemd-analyze", "verify", str(unit)], check=True)
# Tamper only the test-owned unit and ensure explicit recovery does not overwrite.
content = unit.read_text()
unit.write_text(content + "# injected conflict\n")
try:
    nh.register(registry, units, **kwargs, recover=True)
except nh.HelperError as exc:
    assert "conflict" in str(exc)
else:
    raise AssertionError("recovery overwrote conflicting unit")
assert unit.read_text() == content + "# injected conflict\n"
assert record.read_bytes() == saved
print(
    json.dumps(
        {
            "marker_only_recovered": True,
            "public_cli_idempotent": True,
            "unit_conflict_preserved": True,
            "service_started": False,
        }
    )
)
