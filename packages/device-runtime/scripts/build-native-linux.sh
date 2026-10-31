#!/bin/sh
# Build a local, unsigned development package inside a disposable Linux builder.
# Inputs: preassembled payload, locked CPython archive, output directory.
#
# Delivery scope: only the Debian package is a deliverable. Linux/RPM is not a
# declared support target, because the signed update path (native_candidate +
# native_update) only understands Debian packages: its target is fixed to
# linux-amd64-deb, it extracts ar members, and it drives dpkg. A .rpm installs,
# upgrades and erases correctly, but it cannot enter the verified update path, so
# shipping one would hand out a package whose lifecycle is half implemented.
# The rpm branch stays because it is the fastest way to re-check the shared
# layout hooks; it refuses unless DEVICE_NATIVE_ALLOW_UNDELIVERED_RPM=1.
set -eu
[ "$#" -ge 4 ] && [ "$#" -le 6 ] || { echo 'usage: build-native-linux.sh deb|rpm PAYLOAD RUNTIME_ARCHIVE OUTPUT [TREE [PKGVER]]' >&2; exit 2; }
kind=$1; payload=$2; archive=$3; output=$4
# The versioned tree name and the package version are separate knobs so an
# upgrade can be built from identical payload inputs: only its identity differs,
# which is exactly what the pre-install classifier has to judge.
tree=${5:-0.2.2-native1}
pkgver=${6:-0.2.2-1}
case $tree in ''|*[!A-Za-z0-9._-]*) echo 'TREE must be a plain version name' >&2; exit 2 ;; esac
case $pkgver in ''|*[!A-Za-z0-9.+~-]*) echo 'PKGVER must be a plain package version' >&2; exit 2 ;; esac
rpmver=${pkgver%-*}
rpmrel=${pkgver##*-}
[ "$kind" = deb ] || [ "$kind" = rpm ] || exit 2
if [ "$kind" = rpm ] && [ "${DEVICE_NATIVE_ALLOW_UNDELIVERED_RPM:-0}" != 1 ]; then
 echo 'native package build refused: rpm is not a declared support target' >&2
 echo 'only .deb enters the signed update path; set DEVICE_NATIVE_ALLOW_UNDELIVERED_RPM=1 for a layout-only check' >&2
 exit 1
fi
[ "$(uname -m)" = x86_64 ] || exit 2
# Match native/runtime-lock.json before any extraction/execution.
[ "$(wc -c < "$archive" | tr -d ' ')" = 35356674 ] || { echo 'runtime size mismatch' >&2; exit 2; }
printf '%s  %s\n' f829adc9d888e76c8801b4799ed444188d35239fcae3a9808a99ec5cb0d8d92c "$archive" | sha256sum -c -
mkdir -p "$output"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
root=$work/root
home_final=/opt/ai-device/packages/$tree
# The package ships one archive per tree instead of the expanded tree itself.
# A package manager deletes the files it owns before unpacking a new version, so
# owning a published tree would let an upgrade remove the interpreter a running
# caller is executing. Owning a single archive keeps the ownership record true
# (`dpkg -V`/`rpm -V` can compare it) while the post-install step still controls
# when the live tree changes.
#
# The previous layout owned the staging trees instead, which never exist after
# installation: both managers reported the whole payload missing, and every rpm
# upgrade printed one "remove failed" per file.
payload_dir=/opt/ai-device/payloads
payload_tar=$payload_dir/$tree.tar
payload_boot_tar=$payload_dir/$tree-bootstrap.tar
# Assembled in a work path outside the package root, then archived into it.
stage=$work/stage/$tree
stage_boot=$work/stage-boot/$tree
mkdir -p "$stage" "$stage_boot" "$root$payload_dir" "$root/usr/bin"
cp -R "$payload/." "$stage/"
tar -xzf "$archive" -C "$stage"
mv "$stage/python" "$stage/runtime"
site=$stage/runtime/lib/python3.13/site-packages
# The staged tree only runs the installer and the package-level native commands.
# Everything else is installed into the instance venv from bundle/wheels and
# bundle/dependencies, so pre-installing the full dependency set here ships the
# same files twice.
#
# The set below is what installation.py and the signed-release path actually
# reach, not the client's runtime set: install_instance calls client.device_url,
# so client.py imports pydantic and websockets even for an offline install, and
# native_prepare verifies the Ed25519 envelope through cryptography. Pillow and
# zstandard stay out: neither is reachable from these entry points.
rm -rf "$site"
mkdir -p "$site"
required=""
for wheel in "$stage/bundle/wheels/"*.whl; do required="$required $wheel"; done
for name in packaging pydantic pydantic_core annotated_types typing_extensions \
            typing_inspection websockets psutil cryptography cffi pycparser; do
 matches=""
 for wheel in "$stage/bundle/dependencies/$name"-*.whl; do
  [ -f "$wheel" ] && matches="$matches $wheel"
 done
 # Exactly one distribution per name: a name that ships nothing, or ships two
 # builds, would otherwise pass a total-count assertion.
 set -- $matches
 [ "$#" -eq 1 ] || { echo "native package build refused: expected one wheel for $name, found $#" >&2; exit 1; }
 required="$required $matches"
done
env -i PATH=/usr/bin:/bin HOME="$work" TMPDIR="$work" \
 "$stage/tools/uv" pip install --offline --no-cache --no-config --system \
 --python "$stage/runtime/bin/python3.13" --no-index --no-deps --link-mode copy \
 $required > "$work/bootstrap.log" 2>&1 || { cat "$work/bootstrap.log"; exit 1; }
# Components the client never runs: Tcl/Tk, the browser IDE, the bundled pip
# (uv is shipped) and the documentation data are dead weight in every install.
for extra in "$stage/runtime/lib/python3.13/ensurepip" \
             "$stage/runtime/lib/python3.13/idlelib" \
             "$stage/runtime/lib/python3.13/pydoc_data" \
             "$stage/runtime/lib/python3.13/tkinter" \
             "$stage/runtime/lib/python3.13/test" \
             "$stage/runtime/lib/tcl9.0" \
             "$stage/runtime/lib/tk9.0"; do
 rm -rf "$extra"
done
rm -f "$stage/runtime/lib/libtcl"*.so* "$stage/runtime/lib/libtk"*.so* \
      "$stage/runtime/lib/itcl"*.so* "$stage/runtime/lib/thread"*tcl*.so* 2>/dev/null || true
# A missing import only surfaces when the user installs, which is far too late.
# Exercise the real entry point now, inside the staged tree being packaged.
installed=$(du -sk "$site" | cut -f1)
if ! env -i PATH=/usr/bin:/bin HOME="$work" TMPDIR="$work" \
 "$stage/runtime/bin/python3.13" -I -c 'import device_runtime.native_install' \
 > "$work/selfcheck.log" 2>&1; then
 cat "$work/selfcheck.log"
 echo "native package build refused: installer imports are incomplete" >&2
 exit 1
fi
# The installer must also survive the URL validation path that pulls in client.py,
# and native_prepare must be able to verify a signed release: both import paths
# reach modules the bootstrap would otherwise not carry.
if ! env -i PATH=/usr/bin:/bin HOME="$work" TMPDIR="$work" \
 "$stage/runtime/bin/python3.13" -I -c 'from device_runtime.client import device_url; device_url("wss://127.0.0.1:9", "selfcheck"); from device_runtime import native_prepare, native_candidate; import cryptography' \
 >> "$work/selfcheck.log" 2>&1; then
 cat "$work/selfcheck.log"
 echo "native package build refused: client URL validation or release authentication is unavailable" >&2
 exit 1
fi
echo "native bootstrap self-check passed: site-packages ${installed}KB"
cat > "$root/usr/bin/ai-device-native-install" <<'EOF'
#!/bin/sh
set -eu
home=/opt/ai-device/packages/__TREE__
exec "$home/runtime/bin/python3.13" -I -m device_runtime.native_install --payload "$home" "$@"
EOF
sed -i "s#__TREE__#$tree#g" "$root/usr/bin/ai-device-native-install"
chmod 755 "$root/usr/bin/ai-device-native-install"
# Upgrade entry. Unlike the helper wrapper this one resolves the payload tree,
# not the bootstrap: the sequence imports the upgrade modules, which are not
# part of the deliberately minimal bootstrap. The tree is pinned to the version
# that ships this wrapper, so an upgrade never executes the code it replaces.
cat > "$root/usr/bin/ai-device-native-update" <<'EOF'
#!/bin/sh
set -eu
home=/opt/ai-device/packages/__TREE__
if [ ! -x "$home/runtime/bin/python3.13" ]; then
 echo 'native update: this package tree is not installed (payload being replaced); retry' >&2
 exit 75
fi
exec "$home/runtime/bin/python3.13" -I -m device_runtime.native_orchestrate "$@"
EOF
sed -i "s#__TREE__#$tree#g" "$root/usr/bin/ai-device-native-update"
chmod 755 "$root/usr/bin/ai-device-native-update"
# Independent standard-library-only administrator bootstrap, never client imports.
bootstrap=/opt/ai-device/bootstrap/$tree
# Exclude upstream bytecode while creating this new bootstrap, not by altering
# an existing installation. Candidate identity checks require source-only code.
tar --exclude='*/__pycache__' --exclude='*.pyc' --exclude='*.pyo' -xzf "$archive" -C "$stage_boot"
mv "$stage_boot/python" "$stage_boot/runtime"
site="$stage_boot/runtime/lib/python3.13/site-packages"
rm -rf "$site"
mkdir -p "$site/device_runtime"
for module in __init__.py entrypoint.py native_helper.py management_lock.py native_bootstrap.py; do
 cp "$stage/runtime/lib/python3.13/site-packages/device_runtime/$module" "$site/device_runtime/"
done
# The entrypoint resolves the versioned bootstrap through the pointer rather
# than naming one build's tree. A hard-coded path is what made an upgrade leave
# the wrapper pointing at a directory the new package had just deleted.
cat > "$root/usr/bin/ai-device-native-helper" <<'EOF'
#!/bin/sh
set -eu
root=/opt/ai-device/bootstrap
# Same validation the module applies: a single directory name, never a path.
if [ -r "$root/current" ]; then
 version=$(cat "$root/current") || version=
else
 version=__TREE__
fi
case "$version" in
 ''|*[!A-Za-z0-9_.-]*|.*|-*)
  echo 'native helper: bootstrap pointer is invalid' >&2
  exit 1
  ;;
esac
# dpkg and rpm remove the previous payload tree before unpacking the new one, so
# a caller racing an upgrade can observe a pointer that names a tree which is
# briefly absent or incomplete. That is transient, not a refusal or a bug in
# this build. Report it as such with EX_TEMPFAIL (75) so callers can retry
# instead of parsing a shell "exec: not found" message.
#
# This cannot be made atomic: unpacking lands files one at a time, so a probe can
# pass and the interpreter can still lose a module between that probe and exec.
# The checks here cover the common cases where the tree or its interpreter is
# wholly absent, which is what an upgrade actually looks like for most of the
# window. A residual import failure still surfaces as the interpreter's own
# error; do not try to swallow it here, because capturing output would break
# ``serve``, which must stay exec'd.
# Keep 1 for genuine refusal and 2+ for the module's own errors.
interpreter="$root/$version/runtime/bin/python3.13"
if [ ! -f "$interpreter" ] || [ ! -x "$interpreter" ]; then
 echo "native helper: version $version is not installed yet (payload being replaced); retry" >&2
 exit 75
fi
if [ ! -d "$root/$version/runtime/lib/python3.13/site-packages/device_runtime" ]; then
 echo "native helper: version $version payload is incomplete (upgrade in progress); retry" >&2
 exit 75
fi
exec "$interpreter" -I -B -m device_runtime.native_helper "$@"
EOF
sed -i "s#__TREE__#$tree#g" "$root/usr/bin/ai-device-native-helper"
chmod 755 "$root/usr/bin/ai-device-native-helper"
# Upstream CPython files can be group-writable. Published system programs must
# not be writable by the client or group before a root helper trusts them.
chmod -R go-w "$stage"
chmod -R go-w "$stage_boot"
# Building the bootstrap imports CPython modules, which leaves __pycache__ trees
# inside the payload. Bytecode belongs to the running interpreter, not to the
# package, so ship a clean tree and let erase handle only caches created later.
find "$stage" "$stage_boot" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
# Package the finished trees as archives. The expansion happens on the target
# host, in the post-install hook, where the pointer is also moved: the package
# manager only ever writes and removes one file per tree.
#
# No compression: dpkg and rpm already compress their payload, and gzip here
# would only add a decompression pass at install time for no size win.
(cd "$stage" && tar -cf "$root$payload_tar" --owner=0 --group=0 .)
(cd "$stage_boot" && tar -cf "$root$payload_boot_tar" --owner=0 --group=0 .)
chmod 644 "$root$payload_tar" "$root$payload_boot_tar"
# The digest is written by the hooks after expansion, so the published tree can
# be compared against the archive it came from. It ships beside the archive as a
# reference only; nothing may trust it before the expansion succeeds.
# Guard the existing layout with the classifier that ships in this package.
# dpkg runs preinst before unpacking, so our own tree is not on disk yet and the
# only interpreter present belongs to the payload being replaced. An older
# payload predates native_layout, so importing it would fail with a missing
# module on exactly the hosts that need to upgrade. Emit the classifier as a
# standalone script next to the hook and run it with whatever interpreter
# exists: one authoritative source, no dependency on the installed version.
cp "$stage/runtime/lib/python3.13/site-packages/device_runtime/native_layout.py" "$work/classify.py"
cat > "$work/preflight" <<'EOF'
#!/bin/sh
set -eu
here=$(dirname "$0")
classifier=
for candidate in /opt/ai-device/packages/*/runtime/bin/python3.13 \
                 /opt/ai-device/bootstrap/*/runtime/bin/python3.13; do
 [ -x "$candidate" ] || continue
 classifier=$candidate
 break
done
if [ -z "$classifier" ]; then
 # A fresh install has no layout to judge, so only an absent one may proceed.
 # Anything else is state this package cannot inspect: refuse rather than
 # unpack on top of it.
 for path in /opt/ai-device/packages /opt/ai-device/bootstrap \
             /opt/ai-device/instances /var/lib/ai-device/manager; do
  [ ! -e "$path" ] && [ ! -L "$path" ] || {
   echo "native package pre-install refused: $path exists but no payload interpreter can classify it" >&2
   exit 1
  }
 done
 echo "native package pre-install: fresh install" >&2
 exit 0
fi
exec "$classifier" -I -B "$here/classify.py" check __TREE__
EOF
sed -i "s#__TREE__#$tree#g" "$work/preflight"
if [ "$kind" = deb ]; then
 mkdir -p "$root/DEBIAN"
 cat > "$root/DEBIAN/control" <<'EOF'
Package: ai-device-native-client
Version: __PKGVER__
Architecture: amd64
Maintainer: Device development
Description: Offline Device client development package, no service auto-start
EOF
 cp "$work/preflight" "$root/DEBIAN/preinst"
 cp "$work/classify.py" "$root/DEBIAN/classify.py"
 # Publishing the pointer here is what closes the upgrade window on the package
 # side. The package owns one archive per tree; expanding it, renaming the
 # finished tree into place and flipping the pointer all happen here, so the live
 # trees are never partially written: each rename is atomic within the filesystem
 # and the pointer flips only after both trees are in place.
 cat > "$root/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -eu
payload=/opt/ai-device/payloads/__TREE__.tar
payload_boot=/opt/ai-device/payloads/__TREE__-bootstrap.tar
pkg=/opt/ai-device/packages/__TREE__
boot=/opt/ai-device/bootstrap/__TREE__
wanted=__TREE__
mkdir -p /opt/ai-device/packages /opt/ai-device/bootstrap
# The archive is the package's file; the tree is derived from it. Record the
# archive digest beside the tree so a reinstall can tell "already published from
# exactly this archive" from "a different archive now wants the same tree name".
# Directory existence alone was not enough: a reinstall of the same tree name
# would skip expansion and leave the live tree at the old contents while the
# package manager reported the new archive as present.
publish() {
 tar=$1; final=$2
 [ -f "$tar" ] || { echo "native package post-install: payload $tar is missing" >&2; exit 1; }
 digest=$(sha256sum "$tar") || { echo "native package post-install: cannot digest $tar" >&2; exit 1; }
 digest=${digest%% *}
 if [ -d "$final/runtime" ]; then
  if [ -f "$final/.payload-sha256" ] && [ "$(cat "$final/.payload-sha256")" = "$digest" ]; then
   # Already published from this exact archive; a retry only has to finish the
   # pointer.
   return 0
  fi
  echo "native package post-install: $final holds different contents than $tar; remove it or install a new tree name" >&2
  exit 1
 fi
 work=$(mktemp -d /opt/ai-device/.incoming-XXXXXX)
 trap 'rm -rf "$work"' EXIT
 tar -xf "$tar" -C "$work" || { echo "native package post-install: cannot expand $tar" >&2; exit 1; }
 [ -d "$work/runtime" ] || { echo "native package post-install: $tar has no runtime" >&2; exit 1; }
 printf '%s\n' "$digest" > "$work/.payload-sha256"
 chmod 644 "$work/.payload-sha256"
 # A tree that is present but incomplete would be overwritten by this rename,
 # which is what the expansion step is meant to prevent. Refuse instead.
 [ ! -e "$final" ] || { echo "native package post-install: $final exists without a complete runtime" >&2; exit 1; }
 mv "$work" "$final"
 trap - EXIT
}
publish "$payload" "$pkg"
publish "$payload_boot" "$boot"
root=/opt/ai-device/bootstrap
tmp=$(mktemp "$root/.current-XXXXXX")
printf '%s\n' "$wanted" > "$tmp"
chmod 644 "$tmp"
mv -f "$tmp" "$root/current"
# Make the rename durable, not just the file contents. sync on a path is not
# portable (Linux fsyncs the directory via the file descriptor instead), and the
# host has no syncfs helper worth relying on here.
sync
# Retire the trees this package replaced. Instances run from their own
# releases/ copies, and the entrypoint resolves through the pointer, so no
# running thing depends on the old trees; keeping them only costs ~330MB each.
# The pointer was just written, so anything other than $wanted is superseded.
retire() {
 dir=$1; keep=$2
 [ -d "$dir" ] || return 0
 for entry in "$dir"/*; do
  [ -e "$entry" ] || continue
  name=${entry##*/}
  case $name in .*|-*) continue ;; esac
  [ "$name" = "$keep" ] && continue
  # Refuse to follow a link out of the tree: a symlinked entry is not ours to
  # delete and removing its target would be silent data loss.
  if [ -L "$entry" ] || [ ! -d "$entry" ]; then continue; fi
  rm -rf "$entry"
 done
}
retire /opt/ai-device/packages "$wanted"
retire /opt/ai-device/bootstrap "$wanted"
sync
exit 0
EOF
 sed -i "s#__TREE__#$tree#g" "$root/DEBIAN/postinst"
 chmod 755 "$root/DEBIAN/postinst"
 # Nothing owns the published trees: the package owns one archive per tree and
 # %post derives them from it, so an erase has to remove them itself or ~330MB
 # per version is stranded. prerm has already refused the erase while any
 # instance, registration record or unit still exists, so reaching here means no
 # consumer remains.
 cat > "$root/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -eu
# $1 is remove/purge on an erase, update/abort-*/failed-upgrade otherwise. Only
# a real erase drops the trees; an upgrade's new postinst decides what survives.
case "${1:-remove}" in
 remove|purge) ;;
 *) exit 0 ;;
esac
drop() {
 dir=$1
 [ -d "$dir" ] || return 0
 for entry in "$dir"/*; do
  [ -e "$entry" ] || continue
  name=${entry##*/}
  case $name in .*|-*) continue ;; esac
  # Never follow a link out of the tree; a symlink target is not ours to remove.
  if [ -L "$entry" ] || [ ! -d "$entry" ]; then continue; fi
  rm -rf "$entry"
 done
}
# The pointer first: leaving it naming a tree that no longer exists would make
# the next install refuse the layout as broken.
if [ -f /opt/ai-device/bootstrap/current ] && [ ! -L /opt/ai-device/bootstrap/current ]; then
 rm -f /opt/ai-device/bootstrap/current
fi
drop /opt/ai-device/packages
drop /opt/ai-device/bootstrap
# Remove the now-empty parents and the payload directory the package created, so
# the pre-install classifier sees a clean first-install layout afterwards. Rows
# left behind by an interrupted expansion are removed first; the trap in postinst
# handles the normal case, this covers a crash that skipped it.
rm -rf /opt/ai-device/.incoming-* 2>/dev/null || true
rmdir /opt/ai-device/packages /opt/ai-device/bootstrap \
      /opt/ai-device/payloads 2>/dev/null || true
rmdir /opt/ai-device 2>/dev/null || true
exit 0
EOF
 chmod 755 "$root/DEBIAN/postrm"
 sed -i "s#__TREE__#$tree#g" "$root/DEBIAN/preinst"
 chmod 755 "$root/DEBIAN/preinst"
 sed -i "s#__PKGVER__#$pkgver#g" "$root/DEBIAN/control"
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
for cache in /opt/ai-device/packages/__TREE__ /opt/ai-device/bootstrap/__TREE__; do
 [ -d "$cache" ] || continue
 find "$cache" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
done
EOF
 chmod 755 "$root/DEBIAN/prerm"
 sed -i "s#__TREE__#$tree#g" "$root/DEBIAN/prerm"
 dpkg-deb --build "$root" "$output/ai-device-native-client_${pkgver}_amd64.deb"
else
 mkdir -p "$work/rpm/BUILD" "$work/rpm/RPMS" "$work/rpm/SOURCES" "$work/rpm/SPECS" "$work/rpm/SRPMS"
 cat > "$work/rpm/SPECS/native.spec" <<EOF
Name: ai-device-native-client
Version: $rpmver
Release: $rpmrel
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
# The published directories are owned so an erase can remove them when empty,
# but publishing happens in %post: nothing is in them at build time.
mkdir -p %{buildroot}/opt/ai-device/packages %{buildroot}/opt/ai-device/bootstrap
%pretrans -p <lua>
-- Same decision as the dpkg preinst, expressed in lua because rpm runs this
-- before it unpacks anything. Only an absent layout or a single complete
-- versioned tree may proceed; everything else is state we cannot classify.
-- Legacy management trees predate the versioned layout and may still hold live
-- instances that no transaction here maintains. Refuse, exactly as dpkg does.
-- /opt/ai-device/instances is the normal instance root, so its presence is not
-- a reason to refuse: only the legacy administrator state inside it is.
for _,path in ipairs({'/opt/ai-device/instances/manager', '/var/lib/ai-device/manager'}) do
 if posix.stat(path) or posix.readlink(path) then
  error('native package pre-install refused: legacy management layout ' .. path)
 end
end
-- posix.stat follows symlinks, so a link to a directory would pass the type
-- check below and be treated as a versioned tree. Test the link itself first.
local function plain_dir(path)
 if posix.readlink(path) then return false end
 return posix.stat(path, 'type') == 'directory'
end
local function versioned(root)
 local found = {}
 if posix.readlink(root) then
  error('native package pre-install refused: ' .. root .. ' must not be a symlink')
 end
 if not posix.stat(root) then return found end
 if not plain_dir(root) then
  error('native package pre-install refused: ' .. root .. ' is not a directory')
 end
 local names = posix.dir(root)
 if not names then return found end
 for _,name in ipairs(names) do
  if name ~= '.' and name ~= '..' then
   local path = root .. '/' .. name
   -- The bootstrap pointer is a file beside the trees it selects. Any other
   -- non-directory child is state this package must not write over.
   if name == 'current' then
    if posix.readlink(path) then
     error('native package pre-install refused: ' .. path .. ' must be a regular file')
    end
    -- io.open on a directory may succeed or fail depending on libc; check the
    -- type explicitly so the message names the real problem.
    if posix.stat(path, 'type') ~= 'regular' then
     error('native package pre-install refused: ' .. path .. ' must be a regular file')
    end
    local handle = io.open(path, 'r')
    if not handle then
     error('native package pre-install refused: ' .. path .. ' is unreadable')
    end
    local value = handle:read('*l')
    handle:close()
    -- Validate without a pattern: %pretrans runs in a restricted lua where an
    -- anchored match over the file contents proved unreliable, and a wrong
    -- "invalid" answer here blocks every upgrade.
    local ok = type(value) == 'string' and #value > 0 and #value <= 128
    if ok then
     local first = string.sub(value, 1, 1)
     if not string.find('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789', first, 1, true) then
      ok = false
     end
    end
    if ok then
     for i = 1, #value do
      local c = string.sub(value, i, i)
      if not string.find('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-', c, 1, true) then
       ok = false
       break
      end
     end
    end
    if not ok then
     error('native package pre-install refused: ' .. path .. ' names an invalid directory')
    end
   else
    if not plain_dir(path) then
     error('native package pre-install refused: ' .. path .. ' is not a plain directory')
    end
    found[#found + 1] = name
   end
  end
 end
 return found
end
local packages = versioned('/opt/ai-device/packages')
local bootstraps = versioned('/opt/ai-device/bootstrap')
-- Neither root populated is a first install. Otherwise the bootstrap pointer is
-- the only authority: several trees may coexist because a running instance keeps
-- executing the tree it started from, and a new version adds one beside it
-- rather than replacing it. Counting trees here would refuse every upgrade that
-- the dpkg classifier accepts.
local trees = tostring(#packages) .. '/' .. tostring(#bootstraps)
local function has(path)
 local names = posix.dir(path) or {}
 local seen = {}
 for _,name in ipairs(names) do seen[name] = true end
 return seen
end
local function complete(name)
 local payload = has('/opt/ai-device/packages/' .. name)
 if not payload['runtime'] or not payload['bundle'] then return false, 'payload' end
 local boot = has('/opt/ai-device/bootstrap/' .. name)
 if not boot['runtime'] then return false, 'bootstrap' end
 return true
end
if #packages == 0 and #bootstraps == 0 then
 -- An empty layout with a pointer left behind names a tree that is gone; that
 -- is a broken install, not a fresh one.
 local pointer = posix.stat('/opt/ai-device/bootstrap/current', 'type')
 if pointer then
  error('native package pre-install refused: bootstrap pointer survives but no versioned tree does')
 end
 return
end
-- The pointer must name a tree that exists on both sides and is complete.
local handle = io.open('/opt/ai-device/bootstrap/current', 'r')
if not handle then
 error('native package pre-install refused: versioned trees exist but the bootstrap pointer does not name one; found ' .. trees)
end
local live = handle:read('*l')
handle:close()
if type(live) ~= 'string' or #live == 0 then
 error('native package pre-install refused: versioned trees exist but the bootstrap pointer does not name one; found ' .. trees)
end
local ok, side = complete(live)
if not ok then
 error('native package pre-install refused: bootstrap pointer names ' .. live .. ' but its ' .. tostring(side) .. ' is missing or incomplete')
end
if live == '$tree' then
 error('native package pre-install refused: $tree is already installed')
end
%post
# Publish the pointer after the files are in place. The package owns one archive
# per tree; this hook expands it, renames the finished tree into place and only
# then flips the pointer. The dpkg postinst does the same thing, and the two must
# agree, or an upgrade through one manager leaves a working entrypoint while the
# other leaves a broken one.
# The spec is written through an unquoted heredoc so the tree name is baked in,
# which means every other $ must be escaped to survive until install time.
root=/opt/ai-device/bootstrap
payload=/opt/ai-device/payloads/__TREE__.tar
payload_boot=/opt/ai-device/payloads/__TREE__-bootstrap.tar
pkg=/opt/ai-device/packages/__TREE__
boot=/opt/ai-device/bootstrap/__TREE__
mkdir -p /opt/ai-device/packages /opt/ai-device/bootstrap
# Same digest rule as the dpkg postinst: the archive is the package's file, the
# tree is derived from it. Comparing directory existence alone would let a
# reinstall of the same tree name keep the old contents while the package manager
# reported the new archive as installed.
publish() {
 [ -f "\$1" ] || { echo "native package post-install: payload \$1 is missing" >&2; exit 1; }
 digest=\$(sha256sum "\$1") || { echo "native package post-install: cannot digest \$1" >&2; exit 1; }
 digest=\${digest%% *}
 if [ -d "\$2/runtime" ]; then
  if [ -f "\$2/.payload-sha256" ] && [ "\$(cat "\$2/.payload-sha256")" = "\$digest" ]; then
   return 0
  fi
  echo "native package post-install: \$2 holds different contents than \$1; remove it or install a new tree name" >&2
  exit 1
 fi
 work=\$(mktemp -d /opt/ai-device/.incoming-XXXXXX)
 trap 'rm -rf "\$work"' EXIT
 tar -xf "\$1" -C "\$work" || { echo "native package post-install: cannot expand \$1" >&2; exit 1; }
 [ -d "\$work/runtime" ] || { echo "native package post-install: \$1 has no runtime" >&2; exit 1; }
 # One backslash-n: the unquoted heredoc halves each escape, so sh's printf sees
 # \\n and emits a newline. The digest is read back with cat and compared
 # literally, so the trailing newline has to be the real one, not a backslash-n.
 # No backticks or dollar-parens in this block: the spec heredoc is unquoted, so
 # anything that looks like a command substitution runs at build time.
 printf '%s\n' "\$digest" > "\$work/.payload-sha256"
 chmod 644 "\$work/.payload-sha256"
 [ ! -e "\$2" ] || { echo "native package post-install: \$2 exists without a complete runtime" >&2; exit 1; }
 mv "\$work" "\$2"
 trap - EXIT
}
publish "\$payload" "\$pkg"
publish "\$payload_boot" "\$boot"
tmp=\$(mktemp "\$root/.current-XXXXXX")
# One backslash-n, not four. The spec goes through two escapes: the unquoted
# heredoc turns \\\\n into \\n, and sh's printf then turns that into a newline.
# Writing '%s\\\\n' here left the pointer holding a literal backslash-n, so the
# tree name the wrapper read back was never a valid directory name.
printf '%s\n' "__TREE__" > "\$tmp"
chmod 644 "\$tmp"
mv -f "\$tmp" "\$root/current"
# Retire the replaced trees, with the same rule as the dpkg postinst: instances
# run from their own releases/ copies and the entrypoint resolves through the
# pointer, so nothing running depends on them. Skip links rather than deleting
# whatever they point at.
retire() {
 for entry in "\$1"/*; do
  [ -e "\$entry" ] || continue
  name=\${entry##*/}
  case \$name in .*|-*) continue ;; esac
  [ "\$name" = "\$2" ] && continue
  if [ -L "\$entry" ] || [ ! -d "\$entry" ]; then continue; fi
  rm -rf "\$entry"
 done
}
retire /opt/ai-device/packages __TREE__
retire /opt/ai-device/bootstrap __TREE__
sync
exit 0

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
drop_caches('/opt/ai-device/packages/$tree')
drop_caches('/opt/ai-device/bootstrap/$tree')

%postun
# %preun has refused the erase while any instance, registration record or unit
# remains, so a real erase means nothing consumes the payload. The trees are
# derived from the archives by %post and own no rpm file, so rpm leaves them all
# behind; remove them here. rpm passes the count of remaining instances, so an
# upgrade (1 or more) stays under %post's control instead.
if [ "\$1" -gt 0 ] 2>/dev/null; then exit 0; fi
# Drop the pointer first: one naming a vanished tree makes the next install
# refuse the layout as a broken one.
[ ! -f /opt/ai-device/bootstrap/current ] || rm -f /opt/ai-device/bootstrap/current
# Directories left by an interrupted expansion are removed first; the trap in
# %post handles the normal case, this covers a crash that skipped it.
rm -rf /opt/ai-device/.incoming-* 2>/dev/null || true
for dir in /opt/ai-device/packages /opt/ai-device/bootstrap; do
 [ -d "\$dir" ] || continue
 for entry in "\$dir"/*; do
  [ -e "\$entry" ] || continue
  name=\${entry##*/}
  case \$name in .*|-*) continue ;; esac
  # Never follow a link out of the tree; its target is not ours to delete.
  if [ -L "\$entry" ] || [ ! -d "\$entry" ]; then continue; fi
  rm -rf "\$entry"
 done
 # rmdir, not rm -rf: only remove these when empty, so anything a user put here
 # survives and we never take out a directory we did not create.
 rmdir "\$dir" 2>/dev/null || true
done
rmdir /opt/ai-device 2>/dev/null || true
exit 0

%files
# Own the parent directories so erase removes them when empty. Otherwise an
# empty /opt/ai-device/packages left behind by a first erase makes the
# %pretrans guard refuse every later install (dpkg removes its own empty
# parent directories, rpm only removes what %files owns).
%dir /opt/ai-device
# The package owns one archive per tree. %post expands it into the versioned
# tree and flips the pointer, so the ownership record names a file that really
# exists and both managers can verify it.
#
# The earlier layout owned the staging trees instead. They never exist after
# installation, so rpm -V reported every payload file missing and each upgrade
# printed one "remove failed" per file for the previous version's paths. No
# backticks here: this heredoc is unquoted, so they would run at build time.
# %ghost was tried and rejected: it stops shipping the files at all (measured:
# 106MB -> 723KB).
/opt/ai-device/payloads/$tree.tar
/opt/ai-device/payloads/$tree-bootstrap.tar
%dir /opt/ai-device/packages
%dir /opt/ai-device/bootstrap
%dir /opt/ai-device/payloads
/usr/bin/ai-device-native-install
/usr/bin/ai-device-native-update
/usr/bin/ai-device-native-helper
EOF
 sed -i "s#__TREE__#$tree#g" "$work/rpm/SPECS/native.spec"
 rpmbuild --define "_topdir $work/rpm" -bb "$work/rpm/SPECS/native.spec" > "$work/rpm-build.log" 2>&1 || { cat "$work/rpm-build.log"; exit 1; }
 cp "$work/rpm/RPMS/x86_64/"*.rpm "$output/"
fi
