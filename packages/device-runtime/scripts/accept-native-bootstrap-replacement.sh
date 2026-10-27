#!/bin/sh
# Disposable Linux container only: replace a dedicated fixture package, not Device.
set -eu
[ "${DEVICE_DISPOSABLE_CONTAINER:-}" = bootstrap-replacement-v1 ] || { echo 'explicit disposable container marker required' >&2; exit 2; }
[ "$(id -u)" = 0 ] && [ "$(uname -s)" = Linux ] || exit 2
[ "$#" = 2 ] || { echo 'usage: SCRIPT V15_DEB REPOSITORY' >&2; exit 2; }
package=$1; repo=$2
base=/opt/device-bootstrap-acceptance
manager=/var/lib/device-bootstrap-acceptance
[ ! -e "$base" ] && [ ! -e "$manager" ] || { echo 'fresh disposable namespace required' >&2; exit 2; }
mkdir -p "$base/input" "$base/v1/DEBIAN" "$base/v2/DEBIAN" "$manager/transactions"
dpkg-deb -x "$package" "$base/input"
source=/opt/device-bootstrap-fixture/runtime
mkdir -p "$base/v1/opt/device-bootstrap-fixture" "$base/v2/opt/device-bootstrap-fixture"
cp -a "$base/input/opt/ai-device/packages/0.2.2-native1/runtime" "$base/v1/opt/device-bootstrap-fixture/runtime"
for version in 1 2; do
 cat > "$base/v$version/DEBIAN/control" <<EOF
Package: device-bootstrap-disposable-fixture
Version: $version
Architecture: amd64
Maintainer: Local acceptance <acceptance@localhost>
Description: disposable interpreter replacement fixture, not Device
EOF
 echo "$version" > "$base/v$version/opt/device-bootstrap-fixture/version"
 dpkg-deb --build "$base/v$version" "$base/fixture-$version.deb"
done
dpkg --install "$base/fixture-1.deb"
export PYTHONPATH="$repo/packages/device-runtime/src"
"$source/bin/python3.13" -B - "$manager" "$source" "$base/fixture-2.deb" <<'PY'
import fcntl,json,os,subprocess,sys
from pathlib import Path
from device_runtime import native_update as update
from device_runtime import native_bootstrap as bootstrap
manager,source,package=map(Path,sys.argv[1:])
stem=manager/'transactions/replacement'
adapter=update.Debian()
intent=adapter.prepare_execution(stem)
intent['prepared_sha256']='disposable-fixture-not-a-device-approval'
receipt=stem.with_suffix('.sender.json')
with (manager/'execution.lock').open('xb') as lease:
 fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  adapter.apply(package,lease_fd=lease.fileno(),receipt_path=receipt,intent=intent)
 except subprocess.CalledProcessError as exc:
  print(exc.stdout.decode(errors='replace'),file=sys.stderr)
  print(exc.stderr.decode(errors='replace'),file=sys.stderr)
  raise
assert not (source/'bin/python3.13').exists(), 'dpkg did not remove the original interpreter'
assert not (source/'lib/python3.13/os.py').exists(), 'dpkg did not remove source stdlib'
value=json.loads(receipt.read_bytes())
assert value=={'state':'sender_reaped','intent':intent,'returncode':0}
assert adapter.installed('device-bootstrap-disposable-fixture')['version']=='2'
snapshot=stem.with_suffix('.bootstrap')
bootstrap.validate_snapshot(snapshot)
python=snapshot/'runtime/bin/python3.13'
probe=subprocess.run([str(python),'-I','-B','-c','import ssl,sqlite3,ctypes; print("survived")'],check=True,capture_output=True,text=True)
assert probe.stdout.strip()=='survived'
print(json.dumps({'state':'replacement_guard_verified','original_runtime_removed':True,'matched_receipt':True,'snapshot_still_valid':True,'fixture_version':'2','device_package_installed':False}))
PY
