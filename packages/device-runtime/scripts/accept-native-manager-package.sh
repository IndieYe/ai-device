#!/bin/sh
# Extract-only acceptance in a disposable Linux container, never install/service.
set -eu
[ "${DEVICE_DISPOSABLE_CONTAINER:-}" = manager-package-v1 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -s)" = Linux ] || exit 2
[ "$#" = 2 ] || { echo 'usage: SCRIPT NEW_DEB BASELINE_DEB' >&2; exit 2; }
[ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
base=/opt/device-manager-package-acceptance
[ ! -e "$base" ] || exit 2
mkdir -p "$base/new-control" "$base/old-control" /var/lib/ai-device/manager/transactions
dpkg-deb -x "$1" /
dpkg-deb -e "$1" "$base/new-control"
dpkg-deb -e "$2" "$base/old-control"
cmp "$base/new-control/preinst" "$base/old-control/preinst"
if /bin/sh "$base/new-control/preinst" install; then
 echo 'unexpected package hook acceptance' >&2; exit 1
fi
python=/opt/ai-device/packages/0.2.2-native1/runtime/bin/python3.13
"$python" -I -B -m pip --isolated check
"$python" -I -B - "$base" <<'PY'
import hashlib,json,subprocess,sys
from pathlib import Path
from device_runtime import native_bootstrap
modules=['native_update','native_bootstrap','native_update_job','native_candidate','native_prepare']
for name in modules:
 module=__import__('device_runtime.'+name,fromlist=[name])
 assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix))
snapshot=Path('/var/lib/ai-device/manager/transactions/manager-package')
native_bootstrap.snapshot_runtime(Path(sys.prefix),snapshot)
probe=r'''
from pathlib import Path
import sys
from device_runtime import native_update,native_bootstrap,native_update_job,native_candidate,native_prepare
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import zstandard
for module in (native_update,native_bootstrap,native_update_job,native_candidate,native_prepare):
 assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix))
k=Ed25519PrivateKey.generate()
k.public_key().verify(k.sign(b'probe'),b'probe')
assert zstandard.ZstdDecompressor().decompress(zstandard.ZstdCompressor().compress(b'probe'))==b'probe'
print('packaged snapshot crypto/zstd operations passed without source imports')
'''
subprocess.run([str(snapshot/'runtime/bin/python3.13'),'-I','-B','-c',probe],check=True)
native_bootstrap.validate_snapshot(snapshot)
print(json.dumps({'state':'manager_package_verified','preinst_unchanged':True,'overwrite_refused':True,'snapshot_valid':True,'installed':False,'services_started':False}))
PY
