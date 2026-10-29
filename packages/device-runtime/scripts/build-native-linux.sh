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
env -i PATH=/usr/bin:/bin HOME="$work" TMPDIR="$work" \
 "$root$home/tools/uv" pip install --offline --no-cache --no-config --system \
 --python "$root$home/runtime/bin/python3.13" --no-index --no-deps --link-mode copy \
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
# Exclude upstream bytecode while creating this new bootstrap, not by altering
# an existing installation. Candidate identity checks require source-only code.
tar --exclude='*/__pycache__' --exclude='*.pyc' --exclude='*.pyo' -xzf "$archive" -C "$root$bootstrap"
mv "$root$bootstrap/python" "$root$bootstrap/runtime"
site="$root$bootstrap/runtime/lib/python3.13/site-packages"
rm -rf "$site"
mkdir -p "$site/device_runtime"
for module in __init__.py entrypoint.py native_helper.py management_lock.py native_bootstrap.py; do
 cp "$root$home/runtime/lib/python3.13/site-packages/device_runtime/$module" "$site/device_runtime/"
done
cat > "$root/usr/bin/ai-device-native-helper" <<'EOF'
#!/bin/sh
set -eu
exec /opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13 -I -B -m device_runtime.native_helper "$@"
EOF
chmod 755 "$root/usr/bin/ai-device-native-helper"
# Upstream CPython files can be group-writable. Published system programs must
# not be writable by the client or group before a root helper trusts them.
chmod -R go-w "$root$home"
chmod -R go-w "$root$bootstrap"
# Building the bootstrap imports CPython modules, which leaves __pycache__ trees
# inside the payload. Bytecode belongs to the running interpreter, not to the
# package, so ship a clean tree and let erase handle only caches created later.
find "$root$home" "$root$bootstrap" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
# Fail closed: no upgrade/removal of an existing native instance until helper
# transactions are implemented. This development package only supports fresh install.
cat > "$work/preflight" <<'EOF'
#!/bin/sh
set -eu
if [ -e /opt/ai-device/packages ] || [ -L /opt/ai-device/packages ] || [ -e /opt/ai-device/bootstrap ] || [ -L /opt/ai-device/bootstrap ]; then
 echo 'existing native versioned layout; helper transaction required' >&2
 exit 1
fi
if [ -e /opt/ai-device/instances ] || [ -L /opt/ai-device/instances ] || [ -e /var/lib/ai-device/manager ] || [ -L /var/lib/ai-device/manager ] || [ -e /opt/ai-device/bootstrap/0.2.2-native1 ] || [ -L /opt/ai-device/bootstrap/0.2.2-native1 ]; then
 echo 'native lifecycle helper transaction required; no files have been replaced' >&2
 exit 1
fi
if [ -e /opt/ai-device/packages/0.2.2-native1 ] || [ -L /opt/ai-device/packages/0.2.2-native1 ]; then
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
# dpkg calls prerm with a word, not a count: `upgrade` when a new version
# replaces this one, `remove`/`purge` on a real erase. Unlike rpm, dpkg runs
# prerm *before* preinst on an upgrade, so without this branch the guards below
# would fire during an upgrade and report a registered instance where the real
# refusal is the preinst layout check. Only erase must be guarded.
# `set -u` is active, so read the action with a default: an unexpected caller
# that passes nothing is treated as an erase, which keeps the guards closed.
case "${1:-remove}" in
 upgrade|failed-upgrade) exit 0 ;;
esac
# Erasing the package deletes the payload and its bundled interpreter, so an
# existing instance would be left with a run script whose interpreter is gone.
for path in /opt/ai-device/instances /var/lib/ai-device/manager /etc/systemd/system/ai-device-native-helper.service; do
 [ ! -e "$path" ] && [ ! -L "$path" ] || { echo 'explicit instance/service uninstall required' >&2; exit 1; }
done
# A registration record is what ties an instance to this payload. The registry
# directory itself survives unregister (it holds the package lease), so testing
# only for the directory would keep the guard closed forever.
for record in /var/lib/ai-device/native/*.json; do
 [ ! -e "$record" ] || { echo 'registered device instance still uses this payload; unregister it first' >&2; exit 1; }
done
for unit in /etc/systemd/system/ai-device-client-*.service; do
 [ ! -e "$unit" ] && [ ! -L "$unit" ] || { echo 'registered device instance still uses this payload; unregister it first' >&2; exit 1; }
done
# The bundled interpreters write bytecode caches into these trees at run time.
# The package cannot list them, so erase would leave them behind and a later
# install would be refused by the layout guard below. Remove only caches; the
# shipped payload files themselves stay under dpkg's control.
for cache in /opt/ai-device/packages/0.2.2-native1 /opt/ai-device/bootstrap/0.2.2-native1; do
 [ -d "$cache" ] || continue
 find "$cache" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
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
local p = {'/opt/ai-device/instances','/var/lib/ai-device/manager','/opt/ai-device/packages','/opt/ai-device/bootstrap','/opt/ai-device/packages/0.2.2-native1','/opt/ai-device/bootstrap/0.2.2-native1'}
for _,path in ipairs(p) do
 if posix.stat(path) or posix.readlink(path) then error('existing native installation: helper transaction required') end
end
%preun -p <lua>
-- rpm passes the number of package instances that will remain: 0 on a real
-- erase, 1 or more on an upgrade. In a %preun lua script arg[1] is the script
-- name ("<lua>") and arg[2] carries that count as a number, so index 2 and
-- compare numerically. The guards below exist because erasing the payload
-- would strand a registered instance; on an upgrade the new payload replaces
-- this one in place, so neither the guards nor the cache sweep apply.
if arg and tonumber(arg[2]) and tonumber(arg[2]) > 0 then return end
-- Erasing the package deletes the payload and its bundled interpreter, so an
-- existing instance would be left with a run script whose interpreter is gone
-- (systemd then fails with 203/EXEC). Any state that indicates a live or
-- registered instance must refuse the erase; the data itself must be kept.
local guards = {
 '/opt/ai-device/instances',
 '/var/lib/ai-device/manager',
 '/etc/systemd/system/ai-device-native-helper.service'
}
for _,path in ipairs(guards) do
 if posix.stat(path) or posix.readlink(path) then error('explicit instance/service uninstall required') end
end
-- The registry directory survives unregister (it holds the package lease), so
-- the presence of a registration record is what keeps the guard closed.
local records = posix.dir('/var/lib/ai-device/native') or {}
for _,name in ipairs(records) do
 if string.match(name, '%.json$') then
  error('registered device instance still uses this payload; unregister it first')
 end
end
-- Any registered client unit means a device instance still depends on this payload.
local units = posix.dir('/etc/systemd/system') or {}
for _,name in ipairs(units) do
 if string.match(name, '^ai%-device%-client%-.+%.service$') then
  error('registered device instance still uses this payload; unregister it first')
 end
end
-- The bundled interpreters write __pycache__ trees into these directories at
-- run time; the package cannot list them, so erase would otherwise leave them
-- behind and block the next install. Remove only those caches.
local function drop_caches(dir)
 local ok, entries = pcall(posix.dir, dir)
 if not ok or not entries then return end
 for _,name in ipairs(entries) do
  if name ~= '.' and name ~= '..' then
   local child = dir .. '/' .. name
   if name == '__pycache__' then
    local files = posix.dir(child) or {}
    for _,f in ipairs(files) do
     if f ~= '.' and f ~= '..' then posix.unlink(child .. '/' .. f) end
    end
    posix.rmdir(child)
   elseif posix.stat(child) then
    drop_caches(child)
   end
  end
 end
end
drop_caches('/opt/ai-device/packages/0.2.2-native1')
drop_caches('/opt/ai-device/bootstrap/0.2.2-native1')
%files
# Own the parent directories so erase removes them when empty. Otherwise an
# empty /opt/ai-device/packages left behind by a first erase makes the
# %pretrans guard refuse every later install (dpkg removes its own empty
# parent directories, rpm only removes what %files owns).
%dir /opt/ai-device
%dir /opt/ai-device/packages
%dir /opt/ai-device/bootstrap
# The build strips __pycache__ before packaging, so the manifest stays free of
# bytecode caches without per-path excludes.
/opt/ai-device/packages/0.2.2-native1
/usr/bin/ai-device-native-install
/opt/ai-device/bootstrap/0.2.2-native1
/usr/bin/ai-device-native-helper
EOF
 rpmbuild --define "_topdir $work/rpm" -bb "$work/rpm/SPECS/native.spec" > "$work/rpm-build.log" 2>&1 || { cat "$work/rpm-build.log"; exit 1; }
 cp "$work/rpm/RPMS/x86_64/"*.rpm "$output/"
fi
