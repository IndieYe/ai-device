#!/bin/sh
# Fresh disposable, network-isolated Linux container only.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] && [ "$$" = 1 ] || exit 2
pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' b172b35a6882eb61b89b48dba4b27808f3bf6da1d06da8510cb31ca4f2e45d03 "$pkg" | sha256sum -c -
dpkg -i "$pkg"
exec /opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13 -I -B /scripts/probe-native-guard-fd.py
