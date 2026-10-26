#!/bin/sh
# Isolated disposable Ubuntu only. This is a transaction experiment, NOT a Device installer.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = "yes" ] || { echo 'disposable probe opt-in required' >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo 'container root required' >&2; exit 2; }
[ ! -e /opt/ai-device-p1-probe ] || { echo 'probe destination already exists' >&2; exit 2; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
for version in 1 2; do
  root="$work/v$version"
  mkdir -p "$root/DEBIAN" "$root/opt/ai-device-p1-probe"
  cat > "$root/DEBIAN/control" <<EOF
Package: ai-device-p1-transaction-probe
Version: $version
Architecture: amd64
Maintainer: Device local verification
Description: Disposable preinst write-order probe, not a Device release
EOF
  printf '%s\n' "$version" > "$root/opt/ai-device-p1-probe/payload"
  if [ "$version" = 2 ]; then
    cat > "$root/DEBIAN/preinst" <<'EOF'
#!/bin/sh
set -eu
[ "$(cat /opt/ai-device-p1-probe/payload)" = 1 ] || exit 81
# Refuse before unpack. No service action, no arbitrary override switch.
exit 82
EOF
    chmod 755 "$root/DEBIAN/preinst"
  fi
  dpkg-deb --build "$root" "$work/v$version.deb" >/dev/null
done
dpkg -i "$work/v1.deb" >/dev/null
if dpkg -i "$work/v2.deb" >"$work/refusal.log" 2>&1; then
  echo 'ERROR: preinst refusal ignored' >&2
  exit 1
fi
[ "$(cat /opt/ai-device-p1-probe/payload)" = 1 ]
[ "$(dpkg-query -W -f='${Version}' ai-device-p1-transaction-probe)" = 1 ]
dpkg --audit > "$work/audit"
[ ! -s "$work/audit" ]
# A refused update must not prevent an explicit retry of the original version.
dpkg -i "$work/v1.deb" >/dev/null
[ "$(cat /opt/ai-device-p1-probe/payload)" = 1 ]
printf '%s\n' 'PASS: rejected preinst preserved payload and package version; retry converged'
