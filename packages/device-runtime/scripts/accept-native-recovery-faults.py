"""Additional fault acceptance after accept-native-unknown.py, disposable only."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

from device_runtime import native_helper as nh

if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes" or os.geteuid() != 0:
    raise SystemExit("disposable administrator context required")
if Path("/proc/1/comm").read_text().strip() != "systemd":
    raise SystemExit("isolated systemd required")

unit = "ai-device-client-package-probe.service"
registry = Path("/var/lib/ai-device/manager")
subprocess.run(["systemctl", "stop", unit], check=True)
gate = Path("/etc/systemd/system/native-probe-gate.service")
gate.write_text("[Service]\nType=oneshot\nExecStart=/usr/bin/sleep 60\n")
dropin = Path("/etc/systemd/system") / (unit + ".d")
dropin.mkdir(exist_ok=True)
ordering = dropin / "queue.conf"
ordering.write_text("[Unit]\nRequires=native-probe-gate.service\nAfter=native-probe-gate.service\n")
subprocess.run(["systemctl", "daemon-reload"], check=True)


class QueuedStart(nh.Systemd):
    def start(self, name, *, lease_fd=None, receipt_path=None, intent=None):
        assert lease_fd is not None
        subprocess.run([self.executable, "start", "--no-block", name], check=True)
        # This test sender has returned; simulate lost completion while the real
        # target Job is waiting for its dependency. No fake systemd observations.
        nh._save_new(receipt_path, {"state": "sender_reaped", "intent": intent, "returncode": 0})
        raise subprocess.TimeoutExpired("injected queued completion loss", 120)


request = {"action": "start", "instance_id": "package-probe", "operation_id": "queued-loss"}
helper = nh.Helper(registry, adapter=QueuedStart())
assert helper.handle(request, peer_uid=12345)["result"]["state"] == "unknown"
observer = nh.Systemd()
queued = observer.query(unit)
assert queued["ActiveState"] in {"inactive", "failed"} and queued["MainPID"] == "0", queued
assert queued["Job"] not in ("", "0"), queued
recovered = nh.Helper(registry).handle({**request, "action": "resolve-stopped"}, peer_uid=12345)
assert recovered["recovery"]["state"] == "confirmed_stopped"
assert observer.query(unit)["Job"] in ("", "0")
assert observer.query(unit)["MainPID"] == "0"
# Remove only the test dependency, then explicitly retry with a fresh ID.
ordering.unlink()
subprocess.run(["systemctl", "stop", "native-probe-gate.service"], check=True)
subprocess.run(["systemctl", "daemon-reload"], check=True)
assert (
    nh.Helper(registry).handle({**request, "operation_id": "queue-retry"}, peer_uid=12345)[
        "result"
    ]["state"]
    == "started"
)
print(
    json.dumps(
        {
            "queued_job_observed": queued["Job"],
            "queued_job_cancelled": True,
            "explicit_retry": "started",
        }
    )
)

# The fixture sender kills only its own test guard, then exits after a bounded
# delay. No real systemctl start is sent in this injection.
subprocess.run(["systemctl", "stop", unit], check=True)
fixture = Path("/opt/ai-device/test-guard-crash")
marker = Path("/opt/ai-device/test-sender-exited")
ready = marker.with_name("test-sender-ready")
fixture.write_text(
    f"#!{sys.executable}\nimport time\nfrom pathlib import Path\nPath({str(ready)!r}).touch()\ntime.sleep(0.5)\nPath({str(marker)!r}).touch()\n"
)
fixture.chmod(0o700)
crash_request = {**request, "operation_id": "guard-crash"}


class CrashingSender(nh.Systemd):
    def query(self, name):
        return observer.query(name)


crashing = nh.Helper(registry, adapter=CrashingSender(str(fixture)))
original_run = subprocess.run


def crash_guard(argv, **kwargs):
    if len(argv) > 3 and argv[0] == sys.executable and argv[1:3] == ["-I", "-c"]:
        with subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **{k: v for k, v in kwargs.items() if k not in {"check", "capture_output"}},
        ) as guard:
            for _ in range(100):
                if ready.exists():
                    break
                time.sleep(0.01)
            assert ready.exists(), "fixture did not run"
            guard.kill()
            stdout, stderr = guard.communicate(timeout=5)
            raise subprocess.CalledProcessError(guard.returncode, argv, stdout, stderr)
    return original_run(argv, **kwargs)


with patch.object(nh.subprocess, "run", side_effect=crash_guard):
    assert crashing.handle(crash_request, peer_uid=12345)["result"]["state"] == "unknown"
for _ in range(50):
    if marker.exists():
        break
    time.sleep(0.05)
assert marker.exists(), "bounded fixture sender did not exit"
assert not (registry / "sender-package-probe-guard-crash.json").exists()
restarted = nh.Helper(registry)
try:
    restarted.handle({**crash_request, "action": "resolve-stopped"}, peer_uid=12345)
except nh.HelperError as exc:
    assert "receipt missing" in str(exc), str(exc)
else:
    raise AssertionError("guard crash without receipt allowed recovery")
try:
    restarted.handle({**request, "operation_id": "crash-bypass"}, peer_uid=12345)
except nh.HelperError as exc:
    assert "unconfirmed" in str(exc), str(exc)
else:
    raise AssertionError("guard crash allowed a new start")
assert observer.query(unit)["MainPID"] == "0"
print(
    json.dumps(
        {
            "actual_test_guard_sigkill": True,
            "fixture_sender_exited": True,
            "missing_receipt_blocks_recovery_and_retry": True,
        }
    )
)
