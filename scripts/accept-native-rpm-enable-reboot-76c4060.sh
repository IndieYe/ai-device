#!/bin/sh
# RPM counterpart of accept-native-unit-enable-reboot-76c4060.sh.
# Two-phase: phase1 install/register/enable; phase2 after a real container
# restart, without issuing any start command.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] || exit 2
[ -d /run/systemd/system ] || { echo "systemd not running"; exit 2; }

pkg=/input/ai-device-native-client-0.2.2-1.x86_64.rpm
UNIT_DIR=/etc/systemd/system
REGISTRY=/var/lib/ai-device/native
BASE=/opt/ai-device/packages/0.2.2-native1
UNIT=ai-device-client-dev1.service
STATE=/state
PHASE_FILE="$STATE/phase1.done"

phase1() {
    printf '%s  %s\n' "$EXPECTED_SHA256" "$pkg" | sha256sum -c -

    echo "== install =="
    rpm -ivh --nodeps "$pkg" >/dev/null
    # Rocky has no Debian-style adduser; use useradd so uid 12345 is explicit.
    useradd --uid 12345 --user-group --create-home --shell /bin/sh nativedev

    echo "== create instance as non-root =="
    su -s /bin/sh nativedev -c "$BASE/runtime/bin/python3.13 -I -m device_runtime.native_install \
      --payload $BASE --root /home/nativedev/instance --instance-id dev1 \
      --server-url https://center.invalid" >/root/install.json
    cat /root/install.json

    echo "== register =="
    mkdir -p "$REGISTRY"; chmod 755 "$REGISTRY"
    printf 'DEVICE_SERVER_URL=https://center.invalid\nDEVICE_RUNTIME_TOKEN=0123456789abcdef0123456789abcdef\n' > /root/dev1.env
    chmod 600 /root/dev1.env; chown root:root /root/dev1.env
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
    echo "== enable =="
    systemctl is-enabled "$UNIT" 2>&1 || true
    systemctl enable "$UNIT" 2>&1
    systemctl is-enabled "$UNIT" 2>&1

    systemctl start "$UNIT"
    sleep 5
    systemctl show "$UNIT" --property=ActiveState,SubState,MainPID,User,Group,UnitFileState > "$STATE/phase1.show"
    cat "$STATE/phase1.show"

    touch "$PHASE_FILE"
    echo "PHASE1_OK"
}

phase2() {
    echo "== after reboot: unit state without any start command =="
    systemctl show "$UNIT" --property=ActiveState,SubState,MainPID,User,Group,UnitFileState \
        > "$STATE/phase2.show" 2>&1 || true
    cat "$STATE/phase2.show"
    python3 - <<'PY'
import json, os, pwd

acc = pwd.getpwnam("nativedev")
show = {}
for line in open("/state/phase2.show"):
    k, _, v = line.strip().partition("=")
    show[k] = v
report = {
    "unit_file_state": show.get("UnitFileState"),
    "active_state": show.get("ActiveState"),
    "sub_state": show.get("SubState"),
    "main_pid": show.get("MainPID"),
    "expected_uid": acc.pw_uid,
}
pid = show.get("MainPID", "0")
if pid and pid != "0" and os.path.exists(f"/proc/{pid}"):
    st = os.stat(f"/proc/{pid}")
    report["proc_uid"] = st.st_uid
    report["proc_uid_matches"] = st.st_uid == acc.pw_uid
    report["proc_is_root"] = st.st_uid == 0
    report["cwd"] = os.readlink(f"/proc/{pid}/cwd")
else:
    report["proc_state"] = "no main pid after reboot"
print("REBOOT " + json.dumps(report, indent=2, sort_keys=True))
PY
    echo "PHASE2_OK"
}

if [ -f "$PHASE_FILE" ]; then phase2; else phase1; fi
