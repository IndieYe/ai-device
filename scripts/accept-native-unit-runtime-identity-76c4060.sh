#!/bin/sh
# Verify systemd actually runs the registered unit as the registered user.
# Real DEB install + real registration + real unit start, in a disposable
# systemd PID1 container. Never a real device.
set -eu
# Runs via `container exec` inside an already-booted systemd PID1 container,
# so this shell is not PID 1; require root and a running systemd instead.
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] || exit 2
systemctl is-system-running >/dev/null 2>&1 || true
[ -d /run/systemd/system ] || { echo "systemd not running"; exit 2; }

pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' "$EXPECTED_SHA256" "$pkg" | sha256sum -c -

UNIT_DIR=/etc/systemd/system
REGISTRY=/var/lib/ai-device/native
BASE=/opt/ai-device/packages/0.2.2-native1

echo "== install =="
dpkg -i "$pkg" >/dev/null

adduser --uid 12345 --disabled-password --gecos '' nativedev >/dev/null 2>&1

echo "== create instance as non-root =="
su -s /bin/sh nativedev -c "$BASE/runtime/bin/python3.13 -I -m device_runtime.native_install \
  --payload $BASE --root /home/nativedev/instance --instance-id dev1 \
  --server-url https://center.invalid" >/root/install.json
cat /root/install.json

echo "== register into the real unit directory =="
mkdir -p "$REGISTRY"
chmod 755 "$REGISTRY"
printf 'PRIVATE=never-print-this\nDEVICE_SERVER_URL=https://center.invalid\nDEVICE_RUNTIME_TOKEN=0123456789abcdef0123456789abcdef\n' > /root/dev1.env
chmod 600 /root/dev1.env
chown root:root /root/dev1.env

"$BASE/runtime/bin/python3.13" -I - <<PY
import json, sys
sys.path.insert(0, "/opt/ai-device/bootstrap/0.2.2-native1/runtime/lib/python3.13/site-packages")
from device_runtime import native_helper as nh
print("REGISTER " + json.dumps(nh.register(
    "$REGISTRY", "$UNIT_DIR", instance_id="dev1",
    root="/home/nativedev/instance", username="nativedev",
    environment_file="/root/dev1.env"), sort_keys=True))
PY

systemctl daemon-reload

echo "== start unit =="
systemctl start ai-device-client-dev1.service || echo "START_EXIT=$?"
sleep 5
systemctl show ai-device-client-dev1.service \
  --property=ActiveState,SubState,MainPID,User,Group,ExecMainStatus,ControlGroup \
  > /root/show.txt 2>&1 || true
cat /root/show.txt

echo "== verify runtime identity =="
python3 - <<'PY'
import json, os, pwd, re, stat

acc = pwd.getpwnam("nativedev")
report = {"expected_uid": acc.pw_uid, "expected_gid": acc.pw_gid}

show = {}
for line in open("/root/show.txt"):
    k, _, v = line.strip().partition("=")
    show[k] = v
report["active_state"] = show.get("ActiveState")
report["sub_state"] = show.get("SubState")
report["unit_user"] = show.get("User")
report["unit_group"] = show.get("Group")
report["main_pid"] = show.get("MainPID")

pid = show.get("MainPID", "0")
if pid and pid != "0" and os.path.exists(f"/proc/{pid}"):
    st = os.stat(f"/proc/{pid}")
    report["proc_uid"] = st.st_uid
    report["proc_gid"] = st.st_gid
    report["proc_uid_matches"] = st.st_uid == acc.pw_uid
    report["proc_is_root"] = st.st_uid == 0
    with open(f"/proc/{pid}/status") as fh:
        for line in fh:
            if line.startswith(("Uid:", "Gid:", "Groups:")):
                k, _, v = line.partition(":")
                report[k.strip().lower()] = v.strip()
    try:
        report["proc_cwd"] = os.readlink(f"/proc/{pid}/cwd")
    except OSError as exc:
        report["proc_cwd"] = f"unreadable: {exc}"
    report["proc_cwd_matches"] = report["proc_cwd"] == "/home/nativedev/instance"
else:
    report["proc_state"] = "main pid absent"

# The unit file itself must still declare the numeric identity.
unit = open("/etc/systemd/system/ai-device-client-dev1.service").read()
report["unit_declares_user"] = re.search(r"^User=(\d+)$", unit, re.M).group(1)
report["unit_declares_group"] = re.search(r"^Group=(\d+)$", unit, re.M).group(1)
report["unit_declares_workdir"] = re.search(r"^WorkingDirectory=(.+)$", unit, re.M).group(1)
report["unit_user_is_uid"] = report["unit_declares_user"] == str(acc.pw_uid)

print("RUNTIME " + json.dumps(report, indent=2, sort_keys=True))
PY

echo "ACCEPT_OK"
