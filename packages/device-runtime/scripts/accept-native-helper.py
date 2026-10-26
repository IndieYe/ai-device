"""Disposable Linux registration + unit verification, no service start or credentials."""

import importlib.util
import json
import os
import pwd
import subprocess
from pathlib import Path

if os.environ.get("DEVICE_NATIVE_DISPOSABLE_PROBE") != "yes" or os.geteuid() != 0:
    raise SystemExit("disposable container root opt-in required")

source = Path("/task/packages/device-runtime/src/device_runtime/native_helper.py")
spec = importlib.util.spec_from_file_location("native_helper", source)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

base = Path("/opt/native-helper-acceptance")
base.mkdir()  # Never reuse an unknown existing directory.
registry = base / "registry"
units = base / "units"
root = base / "instance"
for directory in [registry, units, root, root / "config"]:
    directory.mkdir()
subprocess.run(["useradd", "-m", "-u", "12345", "nativehelperprobe"], check=True)
account = pwd.getpwnam("nativehelperprobe")
os.chown(root, account.pw_uid, account.pw_gid)
python = Path("/runtime/python/bin/python3.13")
(root / "run").write_text(f"#!{python}\nprint('fixture only; never started')\n")
(root / "run").chmod(0o755)
(root / ".installation.lock").touch()
(root / "instance.json").write_text('{"instance_id":"probe","format_version":1}')
(root / "active.json").write_text('{"roles":["client"]}')
(root / "config/roles.json").write_text('{"client":{}}')
credential_file = base / "fixture.env"
credential_file.write_text("DEVICE_RUNTIME_TOKEN=fixture-not-a-real-device-credential\n")
credential_file.chmod(0o600)
result = helper.register(
    registry,
    units,
    instance_id="probe",
    root=root,
    username=account.pw_name,
    environment_file=credential_file,
)
assert result["started"] is False and result["enabled"] is False
binding = json.loads((registry / "probe.json").read_text())
assert binding["uid"] == 12345
assert "fixture-not-a-real" not in json.dumps(binding)
assert "SendSIGKILL=no" in (units / result["unit"]).read_text()
helper_unit = units / "ai-device-native-helper.service"
helper_unit.write_text(
    helper.render_helper_unit(
        bootstrap_python=python, registry=registry, socket_path=base / "helper.sock", group=12345
    )
)
subprocess.run(
    ["systemd-analyze", "verify", str(units / result["unit"]), str(helper_unit)], check=True
)
try:
    helper.register(
        registry,
        units,
        instance_id="probe",
        root=root,
        username=account.pw_name,
        environment_file=credential_file,
    )
except helper.HelperError:
    pass
else:
    raise AssertionError("duplicate registration silently replaced the binding")
print(
    json.dumps({"registration": result, "unit_verification": "passed", "services_started": False})
)
