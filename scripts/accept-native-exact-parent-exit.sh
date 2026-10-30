#!/bin/sh
# Fresh disposable Linux container only. This is not a production supervisor.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] && [ "$$" = 1 ] || exit 2
pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' b172b35a6882eb61b89b48dba4b27808f3bf6da1d06da8510cb31ca4f2e45d03 "$pkg" | sha256sum -c -
dpkg -i "$pkg"
mkdir -p /var/lib/device-parent-probe/bootstrap-candidates /run/device-parent-probe
chmod 700 /var/lib/device-parent-probe /var/lib/device-parent-probe/bootstrap-candidates /run/device-parent-probe
# No source overlay: execute the bootstrap and candidate from the exact DEB.
exec /opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13 -I -B /scripts/probe-native-verified-parent-exit.py
