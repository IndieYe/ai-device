"""Run after accept-native-systemd.sh in a disposable Linux systemd instance.

Inject lost start completion after the real adapter.start; never touches a host.
The helper module being tested must already be installed in its bootstrap.
"""

import json
import os
import subprocess
from pathlib import Path

from device_runtime import native_helper as nh

if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes" or os.geteuid() != 0:
    raise SystemExit("disposable administrator context required")
if Path("/proc/1/comm").read_text().strip() != "systemd":
    raise SystemExit("isolated systemd required")

unit = "ai-device-client-package-probe.service"
registry = Path("/var/lib/ai-device/manager")


class LostCompletion(nh.Systemd):
    def __init__(self):
        super().__init__()
        self.starts = 0

    def start(self, name, *, lease_fd=None):
        self.starts += 1
        super().start(name, lease_fd=lease_fd)
        raise subprocess.TimeoutExpired("injected lost completion", 120)


# Stop only this disposable test's client, not a real device or service.
subprocess.run(["systemctl", "stop", unit], check=True)
adapter = LostCompletion()
helper = nh.Helper(registry, adapter=adapter)
request = {"action": "start", "instance_id": "package-probe", "operation_id": "lost-completion"}
unknown = helper.handle(request, peer_uid=12345)
assert unknown["result"]["state"] == "unknown"
result_path = registry / "result-package-probe-lost-completion.json"
original = result_path.read_bytes()
pid = subprocess.check_output(
    ["systemctl", "show", unit, "-p", "MainPID", "--value"], text=True
).strip()
assert int(pid) > 1
try:
    helper.handle({**request, "operation_id": "bypass-attempt"}, peer_uid=12345)
except nh.HelperError:
    pass
else:
    raise AssertionError("new ID bypassed unknown")
# New Helper models daemon restart: no in-memory state is required.
restarted = nh.Helper(registry)
recovered = restarted.handle({**request, "action": "resolve-running"}, peer_uid=12345)
assert recovered["recovery"]["state"] == "confirmed_running"
assert recovered["recovery"]["start_replayed"] is False
assert result_path.read_bytes() == original
assert restarted.handle(request, peer_uid=12345)["result"]["state"] == "unknown"
next_start = restarted.handle({**request, "operation_id": "after-recovery"}, peer_uid=12345)
assert next_start["result"]["state"] == "already_active"
assert (
    subprocess.check_output(
        ["systemctl", "show", unit, "-p", "MainPID", "--value"], text=True
    ).strip()
    == pid
)
assert adapter.starts == 1
print(
    json.dumps(
        {
            "unknown_preserved": True,
            "recovery": recovered["recovery"],
            "new_helper_recovered": True,
            "pid_unchanged": pid,
            "actual_start_calls": adapter.starts,
        }
    )
)

# Model an independently failed client in this disposable instance. Disable its
# automatic restart only for this fixture so failure is terminal and observable.
subprocess.run(["systemctl", "stop", unit], check=True)
dropin = Path("/etc/systemd/system") / (unit + ".d")
dropin.mkdir()
(dropin / "failure.conf").write_text("[Service]\nRestart=no\nExecStartPre=/usr/bin/false\n")
subprocess.run(["systemctl", "daemon-reload"], check=True)
failed_request = {**request, "operation_id": "failed-completion"}
failed = restarted.handle(failed_request, peer_uid=12345)
assert failed["result"]["state"] == "unknown"
try:
    restarted.handle({**request, "operation_id": "premature-retry"}, peer_uid=12345)
except nh.HelperError:
    pass
else:
    raise AssertionError("failed unknown allowed premature retry")
failed_result = registry / "result-package-probe-failed-completion.json"
failed_bytes = failed_result.read_bytes()
quiescent = nh.Helper(registry).handle(
    {**failed_request, "action": "resolve-stopped"}, peer_uid=12345
)
assert quiescent["recovery"]["state"] == "confirmed_stopped"
assert failed_result.read_bytes() == failed_bytes
assert restarted.handle(failed_request, peer_uid=12345)["result"]["state"] == "unknown"
# Remove the test-only failure cause; recovery itself never starts the service.
(dropin / "failure.conf").unlink()
subprocess.run(["systemctl", "daemon-reload"], check=True)
retry_request = {**request, "operation_id": "explicit-safe-retry"}
retried = restarted.handle(retry_request, peer_uid=12345)
assert retried["result"]["state"] == "started"
retry_pid = subprocess.check_output(
    ["systemctl", "show", unit, "-p", "MainPID", "--value"], text=True
).strip()
assert int(retry_pid) > 1
restarted.handle(retry_request, peer_uid=12345)
assert (
    subprocess.check_output(
        ["systemctl", "show", unit, "-p", "MainPID", "--value"], text=True
    ).strip()
    == retry_pid
)
print(
    json.dumps(
        {
            "confirmed_stopped": quiescent["recovery"],
            "explicit_retry": "started",
            "retry_same_id_pid_unchanged": retry_pid,
            "original_unknown_preserved": True,
        }
    )
)
