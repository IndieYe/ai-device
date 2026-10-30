#!/bin/sh
# Disposable Rocky9 only. No Device services or user data involved.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = yes ] || exit 2
[ "$(id -u)" = 0 ] || exit 2
[ ! -e /opt/ai-device-p1-probe ] || exit 2
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work"/BUILD "$work"/RPMS "$work"/SOURCES "$work"/SPECS "$work"/SRPMS
for version in 1 2; do
  cat > "$work/SPECS/probe.spec" <<EOF
Name: ai-device-p1-transaction-probe
Version: $version
Release: 1
Summary: Disposable transaction probe, not a Device package
License: MIT
BuildArch: noarch
%description
Disposable RPM pretrans refusal probe.
%install
mkdir -p %{buildroot}/opt/ai-device-p1-probe
echo $version > %{buildroot}/opt/ai-device-p1-probe/payload
%files
/opt/ai-device-p1-probe/payload
EOF
  if [ "$version" = 2 ]; then
    cat >> "$work/SPECS/probe.spec" <<'EOF'
%pretrans -p <lua>
local f = io.open('/opt/ai-device-p1-probe/payload', 'r')
if not f then error('original payload missing') end
local v = f:read('*l'); f:close()
if v ~= '1' then error('original payload changed') end
error('deliberate refusal before unpack')
EOF
  fi
  rpmbuild --define "_topdir $work" -bb "$work/SPECS/probe.spec" > "$work/build-$version.log" 2>&1
done
rpm -i "$work/RPMS/noarch/ai-device-p1-transaction-probe-1-1.noarch.rpm"
if rpm -U "$work/RPMS/noarch/ai-device-p1-transaction-probe-2-1.noarch.rpm" > "$work/refusal.log" 2>&1; then
  echo 'ERROR: pretrans refusal ignored' >&2
  exit 1
fi
[ "$(cat /opt/ai-device-p1-probe/payload)" = 1 ]
[ "$(rpm -q --qf '%{VERSION}' ai-device-p1-transaction-probe)" = 1 ]
rpm -V ai-device-p1-transaction-probe > "$work/verify"
[ ! -s "$work/verify" ]
rpm -U --replacepkgs "$work/RPMS/noarch/ai-device-p1-transaction-probe-1-1.noarch.rpm"
printf '%s\n' 'PASS: rejected pretrans preserved payload and RPM version; retry converged'
