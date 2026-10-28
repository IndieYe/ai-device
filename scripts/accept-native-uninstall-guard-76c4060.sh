#!/bin/sh
# Verify erasing the package refuses while a registered device instance still
# depends on the payload, and that the payload survives the refusal.
#
# This asserts every condition; a non-zero exit means the guard regressed.
# Disposable container only.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] || { echo "must run as root in Linux"; exit 2; }

KIND=${KIND:?set KIND=deb or rpm}
PKG=${PKG:?set PKG to the package path}
EXPECTED_SHA256=${EXPECTED_SHA256:?}
UNIT_DIR=${UNIT_DIR:-/etc/systemd/system}
REGISTRY=${REGISTRY:-/var/lib/ai-device/native}
BASE=/opt/ai-device/packages/0.2.2-native1
SITE=/opt/ai-device/bootstrap/0.2.2-native1/runtime/lib/python3.13/site-packages
UNIT=ai-device-client-dev1.service

fail() { echo "FAIL: $*" >&2; exit 1; }

printf '%s  %s\n' "$EXPECTED_SHA256" "$PKG" | sha256sum -c - >/dev/null || fail "package digest"

echo "== install =="
if [ "$KIND" = deb ]; then dpkg -i "$PKG" >/dev/null; else rpm -ivh --nodeps "$PKG" >/dev/null; fi
getent passwd nativedev >/dev/null 2>&1 || useradd --uid 12345 --user-group --create-home --shell /bin/sh nativedev

echo "== fresh install, nothing registered: erase must be allowed =="
if [ "$KIND" = deb ]; then
    dpkg --purge ai-device-native-client >/dev/null 2>&1 || fail "purge on clean host was refused"
else
    rpm -e ai-device-native-client >/dev/null 2>&1 || fail "erase on clean host was refused"
fi
[ ! -e "$BASE" ] || fail "payload survived erase on clean host"
[ -z "$(find /opt/ai-device -type d 2>/dev/null)" ] || fail "empty layout dirs left behind; a later install would be refused"

echo "== reinstall after clean erase =="
if [ "$KIND" = deb ]; then dpkg -i "$PKG" >/dev/null; else rpm -ivh --nodeps "$PKG" >/dev/null; fi

echo "== create + register an instance =="
rm -rf /home/nativedev/instance
su -s /bin/sh nativedev -c "$BASE/runtime/bin/python3.13 -I -m device_runtime.native_install \
  --payload $BASE --root /home/nativedev/instance --instance-id dev1 \
  --server-url https://center.invalid" >/dev/null || fail "instance creation"
mkdir -p "$REGISTRY"; chmod 755 "$REGISTRY"
printf 'DEVICE_SERVER_URL=https://center.invalid\nDEVICE_RUNTIME_TOKEN=0123456789abcdef0123456789abcdef\n' > /root/dev1.env
chmod 600 /root/dev1.env
"$BASE/runtime/bin/python3.13" -I - <<PY >/dev/null || fail "registration"
import sys
sys.path.insert(0, "$SITE")
from device_runtime import native_helper as nh
nh.register("$REGISTRY", "$UNIT_DIR", instance_id="dev1",
            root="/home/nativedev/instance", username="nativedev",
            environment_file="/root/dev1.env")
PY
[ -e "$UNIT_DIR/$UNIT" ] || fail "unit file was not written"

systemctl daemon-reload
systemctl start "$UNIT"
sleep 4
[ "$(systemctl is-active "$UNIT")" = active ] || fail "instance is not running before erase"

echo "== erase must now be refused =="
set +e
if [ "$KIND" = deb ]; then
    out=$(dpkg --purge ai-device-native-client 2>&1); rc=$?
else
    out=$(rpm -e ai-device-native-client 2>&1); rc=$?
fi
set -e
[ "$rc" -ne 0 ] || fail "erase succeeded while a registered instance exists"
# Two independent guards may fire: the state-directory guard, and the
# registered-unit guard. Either refusal is correct; assert one of them.
if echo "$out" | grep -q 'unregister it first'; then
    echo "refused by registered-unit guard"
else
    echo "$out" | grep -q 'explicit instance/service uninstall required' \
        || fail "refusal reason unexpected: $out"
    echo "refused by instance-state guard"
fi

echo "== payload and instance must survive the refusal =="
[ -e "$BASE" ] || fail "payload was deleted despite refusal"
[ -e /home/nativedev/instance/active.json ] || fail "instance data was deleted"
[ "$(systemctl is-active "$UNIT")" = active ] || fail "running instance was disturbed"
echo "REFUSE_OK"

echo "== drop the registration record, then erase =="
# There is no unregister command yet, so the administrator removal path is
# simulated by deleting the registry record and the unit, which is exactly the
# state the state-directory guard requires.
systemctl stop "$UNIT"
systemctl disable "$UNIT" >/dev/null 2>&1 || true
rm -f "$UNIT_DIR/$UNIT"
rm -rf "$REGISTRY"
systemctl daemon-reload
if [ "$KIND" = deb ]; then
    dpkg --purge ai-device-native-client >/dev/null || fail "purge after registration removal was refused"
else
    rpm -e ai-device-native-client >/dev/null || fail "erase after registration removal was refused"
fi
[ -e /home/nativedev/instance/active.json ] || fail "instance data was removed by package erase"
[ -e /home/nativedev/instance/releases ] || fail "instance releases were removed by package erase"

echo "== no runtime leftovers may remain inside the package-owned trees =="
left=$(find /opt/ai-device/packages /opt/ai-device/bootstrap -type f 2>/dev/null | head -5)
[ -z "$left" ] || fail "package trees left files behind: $left"

echo "== a later install must still be accepted =="
if [ "$KIND" = deb ]; then dpkg -i "$PKG" >/dev/null; else rpm -ivh --nodeps "$PKG" >/dev/null; fi
echo "REINSTALL_AFTER_ERASE_OK"
echo "UNINSTALL_OK"
