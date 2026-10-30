"""Validate sender receipt recovery only inside a disposable systemd container."""
import json
import os
import subprocess
from pathlib import Path

from device_runtime import native_helper as nh

if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes" or os.geteuid() != 0:
    raise SystemExit("disposable administrator context required")
if Path("/proc/1/comm").read_text().strip() != "systemd":
    raise SystemExit("isolated systemd required")

registry = Path("/var/lib/ai-device/manager")
unit = "ai-device-client-package-probe.service"
subprocess.run(["systemctl", "stop", unit], check=True)


class LostCompletion(nh.Systemd):
    def __init__(self):
        super().__init__()
        self.starts = 0

    def start(self, name, *, lease_fd=None, receipt_path=None, intent=None, package_fd=None):
        self.starts += 1
        super().start(
            name, lease_fd=lease_fd, receipt_path=receipt_path, intent=intent, package_fd=package_fd
        )
        raise subprocess.TimeoutExpired("injected lost response", 120)


adapter = LostCompletion()
service = nh.Helper(registry, adapter=adapter)
request = {"action": "start", "instance_id": "package-probe", "operation_id": "receipt-check"}
result = service.handle(request, peer_uid=12345)
assert result["result"]["state"] == "unknown"
receipt_path = registry / "sender-package-probe-receipt-check.json"
original = receipt_path.read_bytes()
receipt = json.loads(original)
assert receipt["state"] == "sender_reaped" and type(receipt["returncode"]) is int
intent_path = registry / "op-package-probe-receipt-check.json"
intent = intent_path.read_bytes()
recovery = registry / "recovery-package-probe-receipt-check.json"
pid = subprocess.check_output(["systemctl", "show", unit, "-p", "MainPID", "--value"], text=True).strip()
assert int(pid) > 1
try:
    invalid_intent = json.loads(intent)
    invalid_intent["execution_barrier"] = True
    intent_path.write_text(json.dumps(invalid_intent))
    try:
        service.handle({**request, "action": "resolve-running"}, peer_uid=12345)
    except nh.HelperError as exc:
        assert "invalid start intent barrier" in str(exc)
    else:
        raise AssertionError("invalid barrier authorized recovery")
    assert not recovery.exists()
finally:
    intent_path.write_bytes(intent)

try:
    receipt["intent"]["execution_barrier"] = 2.0
    receipt_path.write_text(json.dumps(receipt))
    try:
        service.handle({**request, "action": "resolve-running"}, peer_uid=12345)
    except nh.HelperError as exc:
        assert "receipt mismatch" in str(exc)
    else:
        raise AssertionError("type-drifted receipt authorized recovery")
    assert not recovery.exists() and intent_path.read_bytes() == intent
finally:
    receipt_path.write_bytes(original)
receipt = json.loads(original)

try:
    receipt.pop("returncode")
    receipt_path.write_text(json.dumps(receipt))
    try:
        service.handle({**request, "action": "resolve-running"}, peer_uid=12345)
    except nh.HelperError as exc:
        assert "receipt mismatch" in str(exc)
    else:
        raise AssertionError("malformed receipt authorized recovery")
    assert not recovery.exists() and intent_path.read_bytes() == intent
    try:
        service.handle({**request, "operation_id": "receipt-bypass"}, peer_uid=12345)
    except nh.HelperError as exc:
        assert "unconfirmed" in str(exc)
    else:
        raise AssertionError("malformed receipt allowed a new start")
finally:
    # This fixture deliberately modified only its own disposable receipt.
    receipt_path.write_bytes(original)

resolved = nh.Helper(registry).handle({**request, "action": "resolve-running"}, peer_uid=12345)
assert resolved["recovery"]["state"] == "confirmed_running"
assert resolved["recovery"]["start_replayed"] is False
assert intent_path.read_bytes() == intent and adapter.starts == 1
assert subprocess.check_output(["systemctl", "show", unit, "-p", "MainPID", "--value"], text=True).strip() == pid
print("SYSTEMD_INVALID_BARRIER_AND_RECEIPT_REFUSED", flush=True)
print("SYSTEMD_RECEIPT_UNKNOWN_RECOVERY_OK", flush=True)
