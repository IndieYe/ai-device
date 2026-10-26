#!/bin/sh
# Build a local, unsigned development package inside a disposable Linux builder.
# Inputs: preassembled payload, locked CPython archive, output directory.
set -eu
[ "$#" = 4 ] || { echo 'usage: build-native-linux.sh deb|rpm PAYLOAD RUNTIME_ARCHIVE OUTPUT' >&2; exit 2; }
kind=$1; payload=$2; archive=$3; output=$4
[ "$kind" = deb ] || [ "$kind" = rpm ] || exit 2
[ "$(uname -m)" = x86_64 ] || exit 2
# Match native/runtime-lock.json before any extraction/execution.
[ "$(wc -c < "$archive" | tr -d ' ')" = 35356674 ] || { echo 'runtime size mismatch' >&2; exit 2; }
printf '%s  %s\n' f829adc9d888e76c8801b4799ed444188d35239fcae3a9808a99ec5cb0d8d92c "$archive" | sha256sum -c -
mkdir -p "$output"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
root=$work/root
home=/opt/ai-device/packages/0.2.2-native1
mkdir -p "$root$home" "$root/usr/bin"
cp -R "$payload/." "$root$home/"
tar -xzf "$archive" -C "$root$home"
mv "$root$home/python" "$root$home/runtime"
"$root$home/runtime/bin/python3.13" -I -m pip --isolated install --no-index --no-deps \
 --find-links "$root$home/bundle/dependencies" \
 "$root$home/bundle/wheels/"*.whl "$root$home/bundle/dependencies/"*.whl > "$work/bootstrap.log" 2>&1
cat > "$root/usr/bin/ai-device-native-install" <<'EOF'
#!/bin/sh
set -eu
home=/opt/ai-device/packages/0.2.2-native1
exec "$home/runtime/bin/python3.13" -I -m device_runtime.native_install --payload "$home" "$@"
EOF
chmod 755 "$root/usr/bin/ai-device-native-install"
# Independent standard-library-only administrator bootstrap, never client imports.
bootstrap=/opt/ai-device/bootstrap/0.2.2-native1
mkdir -p "$root$bootstrap"
tar -xzf "$archive" -C "$root$bootstrap"
mv "$root$bootstrap/python" "$root$bootstrap/runtime"
site="$root$bootstrap/runtime/lib/python3.13/site-packages"
rm -rf "$site"
mkdir -p "$site/device_runtime"
for module in __init__.py native_helper.py management_lock.py; do
 cp "$root$home/runtime/lib/python3.13/site-packages/device_runtime/$module" "$site/device_runtime/"
done
cat > "$root/usr/bin/ai-device-native-helper" <<'EOF'
#!/bin/sh
set -eu
exec /opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13 -I -m device_runtime.native_helper "$@"
EOF
chmod 755 "$root/usr/bin/ai-device-native-helper"
# Upstream CPython files can be group-writable. Published system programs must
# not be writable by the client or group before a root helper trusts them.
chmod -R go-w "$root$home"
chmod -R go-w "$root$bootstrap"
# Fail closed: no upgrade/removal of an existing native instance until helper
# transactions are implemented. This development package only supports fresh install.
cat > "$work/preflight" <<'EOF'
#!/bin/sh
set -eu
if [ -e /opt/ai-device/instances ] || [ -e /var/lib/ai-device/manager ] || [ -e /opt/ai-device/bootstrap/0.2.2-native1 ]; then
 echo 'native lifecycle helper transaction required; no files have been replaced' >&2
 exit 1
fi
if [ -e /opt/ai-device/packages/0.2.2-native1 ]; then
 echo 'existing native payload; in-place package replacement is not yet supported' >&2
 exit 1
fi
EOF
if [ "$kind" = deb ]; then
 mkdir -p "$root/DEBIAN"
 cat > "$root/DEBIAN/control" <<'EOF'
Package: ai-device-native-client
Version: 0.2.2-1
Architecture: amd64
Maintainer: Device development
Description: Offline Device client development package, no service auto-start
EOF
 cp "$work/preflight" "$root/DEBIAN/preinst"
 chmod 755 "$root/DEBIAN/preinst"
 # Preserve any user-created instance; the only package-owned tree is packages/.
 cat > "$root/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -eu
for path in /opt/ai-device/instances /var/lib/ai-device/manager /etc/systemd/system/ai-device-native-helper.service; do
 [ ! -e "$path" ] || { echo 'explicit instance/service uninstall required' >&2; exit 1; }
done
EOF
 chmod 755 "$root/DEBIAN/prerm"
 dpkg-deb --build "$root" "$output/ai-device-native-client_0.2.2-1_amd64.deb"
else
 mkdir -p "$work/rpm/BUILD" "$work/rpm/RPMS" "$work/rpm/SOURCES" "$work/rpm/SPECS" "$work/rpm/SRPMS"
 cat > "$work/rpm/SPECS/native.spec" <<EOF
Name: ai-device-native-client
Version: 0.2.2
Release: 1
Summary: Offline Device client development package
# Deliberately do not claim composite runtime third-party licensing is closed.
License: MIT AND Python-2.0 AND LicenseRef-Development-Review-Pending
BuildArch: x86_64
AutoReqProv: no
%global __os_install_post %{nil}
%description
Offline development payload. Service management and unattended update not enabled.
%install
mkdir -p %{buildroot}
cp -a $root/. %{buildroot}/
%pretrans -p <lua>
local p = {'/opt/ai-device/instances','/var/lib/ai-device/manager','/opt/ai-device/packages/0.2.2-native1','/opt/ai-device/bootstrap/0.2.2-native1'}
for _,path in ipairs(p) do
 if posix.stat(path) then error('existing native installation: helper transaction required') end
end
%preun -p <lua>
if posix.stat('/opt/ai-device/instances') then error('explicit instance/service uninstall required') end
if posix.stat('/var/lib/ai-device/manager') or posix.stat('/etc/systemd/system/ai-device-native-helper.service') then error('explicit helper uninstall required') end
%files
/opt/ai-device/packages/0.2.2-native1
/usr/bin/ai-device-native-install
/opt/ai-device/bootstrap/0.2.2-native1
/usr/bin/ai-device-native-helper
EOF
 rpmbuild --define "_topdir $work/rpm" -bb "$work/rpm/SPECS/native.spec" > "$work/rpm-build.log" 2>&1 || { cat "$work/rpm-build.log"; exit 1; }
 cp "$work/rpm/RPMS/x86_64/"*.rpm "$output/"
fi
