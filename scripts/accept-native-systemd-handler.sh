#!/bin/sh
# Run only inside a disposable, network-isolated systemd container.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = yes ] && [ "$(id -u)" = 0 ] && [ "$(cat /proc/1/comm)" = systemd ] || exit 2
pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' b172b35a6882eb61b89b48dba4b27808f3bf6da1d06da8510cb31ca4f2e45d03 "$pkg" | sha256sum -c -
dpkg -i "$pkg"
cat > /run/systemd/system/ai-device-native-helper.service <<'UNIT'
[Unit]
Description=Disposable peer identity counterexample
[Service]
Type=simple
Environment=DEVICE_NATIVE_DISPOSABLE_PROBE=yes
ExecStart=/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13 -I -B /scripts/probe-native-systemd-handler.py serve
UNIT
systemctl daemon-reload
systemctl start ai-device-native-helper.service
exec /opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13 -I -B /scripts/probe-native-systemd-handler.py observe
