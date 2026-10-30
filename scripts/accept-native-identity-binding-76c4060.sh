#!/bin/sh
# End-to-end identity binding acceptance: real DEB install, then real registration.
# Disposable, network-isolated Linux container only. Never a real device.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] && [ "$$" = 1 ] || exit 2

pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' "$EXPECTED_SHA256" "$pkg" | sha256sum -c -

echo "== install =="
dpkg -i "$pkg" >/dev/null

uid=12345
user=nativedev
adduser --uid "$uid" --disabled-password --gecos '' "$user" >/dev/null 2>&1
base=/opt/ai-device/packages/0.2.2-native1
runtime=$base/runtime

echo "== install instance as non-root =="
su -s /bin/sh "$user" -c "$base/runtime/bin/python3.13 -I -m device_runtime.native_install \
  --payload $base --root /home/$user/instance --instance-id dev1 \
  --server-url https://center.invalid" >/root/install.json
cat /root/install.json

echo "== registration as root =="
roots=/var/lib/ai-device/native
mkdir -p "$roots/units"
chmod 755 "$roots" "$roots/units"
printf 'PRIVATE=never-print-this\n' > /root/dev1.env
chown root:root /root/dev1.env
chmod 600 /root/dev1.env

"$runtime/bin/python3.13" -I - <<'PY'
import json, os, pwd, sys
sys.path.insert(0, "/opt/ai-device/bootstrap/0.2.2-native1/runtime/lib/python3.13/site-packages")
from device_runtime import native_helper as nh

registry = "/var/lib/ai-device/native"
unit_dir = registry + "/units"
print("helper_module", nh.__file__)
result = nh.register(
    registry,
    unit_dir,
    instance_id="dev1",
    root="/home/nativedev/instance",
    username="nativedev",
    environment_file="/root/dev1.env",
)
print("REGISTER_RESULT " + json.dumps(result, sort_keys=True, default=str))
PY

echo "== evidence =="
python3 - <<'PY'
import json, os, pwd, stat, sys
sys.path.insert(0, "/opt/ai-device/bootstrap/0.2.2-native1/runtime/lib/python3.13/site-packages")
from device_runtime import native_helper as nh
acc = pwd.getpwnam("nativedev")
registry = "/var/lib/ai-device/native"
report = {"expected_uid": acc.pw_uid}

record = json.load(open(f"{registry}/dev1.json"))
report["record_uid"] = record["uid"]
report["record_uid_matches_account"] = record["uid"] == acc.pw_uid
report["record_uid_non_root"] = record["uid"] > 0
report["record_root"] = record["root"]

marker = json.load(open("/home/nativedev/instance/" + nh.MARKER))
binding = marker["binding"]
report["marker_uid"] = binding["uid"]
report["marker_uid_matches_account"] = binding["uid"] == acc.pw_uid
report["marker_schema"] = marker["schema_version"]

root_info = os.stat("/home/nativedev/instance")
report["instance_owner_is_account"] = root_info.st_uid == acc.pw_uid
# Report the bits separately: 0o022 conflates group and world write, and the
# instance directory is legitimately 0775 owned by the account's own group.
report["instance_world_writable"] = bool(root_info.st_mode & 0o002)
report["instance_group_writable"] = bool(root_info.st_mode & 0o020)
report["instance_mode"] = oct(root_info.st_mode & 0o777)
report["registry_owner_root"] = os.stat(registry).st_uid == 0
report["registry_world_writable"] = bool(os.stat(registry).st_mode & 0o002)

unit = record["unit_path"]
report["unit_exists"] = os.path.exists(unit)
report["unit_owner_root"] = os.stat(unit).st_uid == 0
report["unit_regular"] = stat.S_ISREG(os.stat(unit).st_mode)

env_mode = os.stat("/root/dev1.env").st_mode & 0o777
report["env_mode_0600"] = env_mode == 0o600

print("IDENTITY " + json.dumps(report, indent=2, sort_keys=True))
PY

echo "== reject: duplicate registration =="
"$runtime/bin/python3.13" -I - <<'PY'
import sys
sys.path.insert(0, "/opt/ai-device/bootstrap/0.2.2-native1/runtime/lib/python3.13/site-packages")
from device_runtime import native_helper as nh
try:
    nh.register("/var/lib/ai-device/native", "/var/lib/ai-device/native/units",
                instance_id="dev1", root="/home/nativedev/instance",
                username="nativedev", environment_file="/root/dev1.env")
except (nh.HelperError, ValueError) as exc:
    print("DUP_REFUSED " + type(exc).__name__ + ": " + str(exc))
else:
    print("DUP_NOT_REFUSED")
PY

echo "ACCEPT_OK"
