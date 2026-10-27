#!/bin/sh
# Fresh disposable Linux container only; no real Device/service operations.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] && [ "$$" = 1 ] || exit 2
pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' b172b35a6882eb61b89b48dba4b27808f3bf6da1d06da8510cb31ca4f2e45d03 "$pkg" | sha256sum -c -
mkdir /tmp/control
dpkg-deb -e "$pkg" /tmp/control
[ -f /tmp/control/preinst ] && [ -f /tmp/control/prerm ]
grep -q 'native lifecycle helper transaction required' /tmp/control/preinst
grep -q 'explicit instance/service uninstall required' /tmp/control/prerm
dpkg -i "$pkg"
base=/opt/ai-device/packages/0.2.2-native1/runtime/lib/python3.13/site-packages/device_runtime
bootstrap=/opt/ai-device/bootstrap/0.2.2-native1/runtime/lib/python3.13/site-packages/device_runtime
for module in __init__.py native_bootstrap.py native_helper.py management_lock.py native_update.py native_update_job.py; do
 cmp "/source/$module" "$base/$module"
done
for module in __init__.py native_bootstrap.py native_helper.py management_lock.py; do
 cmp "/source/$module" "$bootstrap/$module"
done
/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13 -I -B - <<'PY'
from device_runtime import native_helper as h
intent = {"action": "start", "operation_id": "one"}
assert h._valid_sender_completion({"state": "sender_reaped", "intent": intent, "returncode": -9}, intent)
for bad in (None, True, "0", 1.5):
    assert not h._valid_sender_completion({"state": "sender_reaped", "intent": intent, "returncode": bad}, intent)
assert not h._valid_sender_completion({"state": "sender_reaped", "intent": intent}, intent)
print("CURRENT_DEB_RECEIPT_GUARD_OK", flush=True)
PY
# Reinstall must fail: package protection must remain in place.
if dpkg -i "$pkg" >/tmp/reinstall.log 2>&1; then
 echo 'unexpected in-place replacement' >&2
 exit 1
fi
grep -q 'helper transaction required' /tmp/reinstall.log
echo CURRENT_DEB_FRESH_INSTALL_AND_PROTECTION_OK
