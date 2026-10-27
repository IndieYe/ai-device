#!/bin/sh
# Only in a disposable, isolated Linux container; not production deployment.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] && [ "$$" = 1 ] || exit 2
pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' 909d10088315e1e64bfb9fdaad264ee41c6b71797d1032e43752c53a494c1d2a "$pkg" | sha256sum -c -
dpkg -i "$pkg"
base=/opt/ai-device/bootstrap/0.2.2-native1/runtime/lib/python3.13/site-packages/device_runtime
# Older test package plus explicitly overlaid current source: not an exact new build.
for module in __init__.py native_bootstrap.py native_helper.py management_lock.py; do
    cp "/source/$module" "$base/$module"
    cmp "/source/$module" "$base/$module"
done
mkdir -p /var/lib/device-parent-probe/bootstrap-candidates /run/device-parent-probe
chmod 700 /var/lib/device-parent-probe /var/lib/device-parent-probe/bootstrap-candidates /run/device-parent-probe
exec /opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13 -I -B /scripts/probe-native-verified-parent-exit.py
