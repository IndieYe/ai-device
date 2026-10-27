"""Read-only peer observation in a disposable systemd container, never authorization."""
import json
import os
import socket
import subprocess
from pathlib import Path

from device_runtime import native_helper as helper

if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes" or os.geteuid() != 0:
    raise SystemExit("disposable administrator context required")
if Path("/proc/1/comm").read_text().strip() != "systemd":
    raise SystemExit("isolated systemd required")

endpoint = "/run/ai-device-helper/helper.sock"
pid = int(subprocess.check_output(
    ["systemctl", "show", "ai-device-native-helper.service", "-p", "MainPID", "--value"],
    text=True,
).strip())
assert pid > 1
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(5)
    connection.connect(endpoint)
    with helper.scoped_helper_unit_peer(connection) as observation:
        assert observation["pid"] == pid
        assert observation["loaded_code_verified"] is False
        assert observation["dispatch_allowed"] is False
        request = {"action": "query", "instance_id": "package-probe", "operation_id": "peer-observe"}
        connection.sendall(json.dumps(request).encode() + b"\n")
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = connection.recv(4096)
            if not chunk or len(response) + len(chunk) > 65536:
                raise RuntimeError("invalid bounded query response")
            response.extend(chunk)
        assert json.loads(response)["ok"] is True
print("SYSTEMD_PEER_OBSERVED_NOT_AUTHORIZED_OK", flush=True)
