"""Disposable Linux package/bootstrap and real peer-credential acceptance.

Does not start a systemd service or connect to any center. A root subprocess
runs the helper only for the duration of this isolated test.
"""

import json
import os
import subprocess
import time
from pathlib import Path

if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes" or os.geteuid() != 0:
    raise SystemExit("disposable root container required")

bootstrap = Path("/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13")
python = str(bootstrap)
helper = "/usr/bin/ai-device-native-helper"
registry = Path("/var/lib/ai-device/manager")
registry.parent.mkdir(mode=0o755, exist_ok=True)
assert subprocess.run([helper, "--help"], check=False, capture_output=True).returncode == 0
# No client dependencies or mutable client release are used by the root helper.
subprocess.run(
    [
        python,
        "-I",
        "-c",
        "import importlib.util; from device_runtime import native_helper; assert importlib.util.find_spec('pydantic') is None; print(native_helper.__file__)",
    ],
    check=True,
)
# Client runtime is intentionally unavailable while provisioning the helper.
client = Path("/opt/ai-device/packages/0.2.2-native1")
moved = client.with_name("client-unavailable")
client.rename(moved)
try:
    deployment = json.loads(
        subprocess.check_output(
            [
                helper,
                "deploy",
                "--bootstrap-python",
                python,
                "--registry",
                str(registry),
                "--unit-dir",
                "/etc/systemd/system",
                "--runtime-dir",
                "/run/ai-device-helper",
                "--group",
                "12345",
            ]
        )
    )
    assert deployment["started"] is False and deployment["enabled"] is False
    duplicate = subprocess.run(
        [
            helper,
            "deploy",
            "--bootstrap-python",
            python,
            "--registry",
            str(registry),
            "--unit-dir",
            "/etc/systemd/system",
            "--runtime-dir",
            "/run/ai-device-helper",
            "--group",
            "12345",
        ],
        check=False,
        capture_output=True,
    )
    assert duplicate.returncode != 0 and b"already exists" in duplicate.stderr
finally:
    moved.rename(client)
subprocess.run(["systemd-analyze", "verify", deployment["unit"]], check=True)
subprocess.run(["useradd", "-m", "-u", "12345", "nativeprobe"], check=True)
subprocess.run(["useradd", "-m", "-u", "12346", "otherprobe"], check=True)
# Both users can reach the socket group; only the registered UID may act.
subprocess.run(["usermod", "-a", "-G", "nativeprobe", "otherprobe"], check=True)
root = Path("/opt/ai-device/instances/probe")
root.parent.mkdir(mode=0o755)
os.chown(root.parent, 12345, 12345)
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
        "package-probe",
        "--server-url",
        "http://127.0.0.1:9",
    ],
    check=True,
)
credential = Path("/opt/ai-device/fixture.env")
credential.write_text("DEVICE_RUNTIME_TOKEN=fixture-not-real\n")
credential.chmod(0o600)
registered = json.loads(
    subprocess.check_output(
        [
            helper,
            "register",
            "--registry",
            str(registry),
            "--unit-dir",
            "/etc/systemd/system",
            "--root",
            str(root),
            "--instance-id",
            "package-probe",
            "--username",
            "nativeprobe",
            "--environment-file",
            str(credential),
        ]
    )
)
subprocess.run(
    ["systemd-analyze", "verify", "/etc/systemd/system/" + registered["unit"]], check=True
)
socket = Path("/run/ai-device-helper/helper.sock")
process = subprocess.Popen(
    [helper, "serve", "--registry", str(registry), "--socket", str(socket), "--group", "12345"]
)
try:
    for _ in range(100):
        if socket.exists():
            break
        if process.poll() is not None:
            raise AssertionError("helper exited before socket publication")
        time.sleep(0.05)
    assert socket.exists()
    busy_recovery = subprocess.run(
        [helper, "recover-socket", "--registry", str(registry), "--socket", str(socket)],
        check=False,
        capture_output=True,
    )
    assert busy_recovery.returncode != 0 and socket.exists()

    def call(user):
        return subprocess.run(
            [
                "runuser",
                "-u",
                user,
                "--",
                helper,
                "call",
                "--socket",
                str(socket),
                "--action",
                "query",
                "--instance-id",
                "package-probe",
                "--operation-id",
                "peer-probe",
            ],
            check=False,
            capture_output=True,
        )

    denied = call("otherprobe")
    assert denied.returncode != 0, denied.stdout
    owner = call("nativeprobe")
    # No systemd manager is running in this acceptance mode. Owner reaches the
    # real systemctl observation; it must fail, not fabricate an active service.
    assert owner.returncode != 0, owner.stdout
    assert json.loads(denied.stdout) == {"ok": False, "error": "helper_request_refused"}
    assert json.loads(owner.stdout) == {"ok": False, "error": "helper_request_refused"}
    print(
        json.dumps(
            {
                "bootstrap_independent": True,
                "deployment": deployment,
                "real_socket_nonowner": "refused via SO_PEERCRED",
                "systemd_service_started": False,
                "owner_query_without_systemd": "refused",
            }
        )
    )
finally:
    # This is only the test-owned temporary daemon, never a real service.
    process.terminate()
    process.wait(timeout=10)
# Existing endpoint is preserved and a second daemon must not unlink it.
assert socket.exists()
retry = subprocess.run(
    [helper, "serve", "--registry", str(registry), "--socket", str(socket), "--group", "12345"],
    check=False,
    capture_output=True,
)
assert retry.returncode != 0 and socket.exists()
recovered = json.loads(
    subprocess.check_output(
        [
            helper,
            "recover-socket",
            "--registry",
            str(registry),
            "--socket",
            str(socket),
        ]
    )
)
assert recovered == {"removed": True, "operations_changed": False}
assert not socket.exists()
print("orphan socket preserved until explicit administrator recovery; active daemon refused")
