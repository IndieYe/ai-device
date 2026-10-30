#!/bin/sh
# Disposable empty Linux container ONLY: real package manager mutates its database.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = layout-guard-v1 ] || exit 2
[ "$#" = 3 ] || { echo 'usage: SCRIPT deb|rpm PACKAGE existing|dangling|fresh' >&2; exit 2; }
kind=$1; package=$2; mode=$3
[ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
case "$kind" in
 deb) install() { dpkg -i "$package"; }; remove() { dpkg -r ai-device-native-client; } ;;
 rpm) install() { rpm -i --nodeps "$package"; }; remove() { rpm -e --nodeps ai-device-native-client; } ;;
 *) exit 2 ;;
esac
case "$mode" in
 existing)
  mkdir -p /opt/ai-device/packages/old-release
  printf 'sentinel\n' > /opt/ai-device/packages/old-release/data
  if install >/tmp/native-layout-install.log 2>&1; then echo 'unexpected install success' >&2; exit 1; fi
  # The payload directory exists but carries no interpreter, so nothing can
  # classify it. That stays a refusal; the message names the reason.
  grep 'no payload interpreter can classify it' /tmp/native-layout-install.log
  test "$(cat /opt/ai-device/packages/old-release/data)" = sentinel
  test ! -e /opt/ai-device/packages/0.2.2-native1
  ;;
 dangling)
  mkdir -p /opt/ai-device
  ln -s /missing-target /opt/ai-device/bootstrap
  if install >/tmp/native-layout-install.log 2>&1; then echo 'unexpected install success' >&2; exit 1; fi
  # A dangling symlink is an unclassifiable layout, not an empty one.
  grep 'no payload interpreter can classify it' /tmp/native-layout-install.log
  test "$(readlink /opt/ai-device/bootstrap)" = /missing-target
  test ! -e /opt/ai-device/packages
  ;;
 fresh)
  install >/tmp/native-layout-install.log 2>&1
  test -x /opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
  mkdir -p /var/lib/ai-device/manager
  if remove >/tmp/native-layout-remove.log 2>&1; then echo 'unexpected remove success' >&2; exit 1; fi
  grep 'explicit .* uninstall required' /tmp/native-layout-remove.log
  test -x /opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
  ;;
 *) exit 2 ;;
esac
printf 'LAYOUT_GUARD_OK kind=%s mode=%s\n' "$kind" "$mode"
