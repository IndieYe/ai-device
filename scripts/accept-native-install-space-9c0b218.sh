#!/bin/sh
# Only a disposable offline Linux container; never use a real host.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = install-space-9c0b218 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 1ea705f62d275f87ce63a67ba9ef8a8930c0b56fbd339a978e4ea4478564b6c3 "$1" | sha256sum -c -
dpkg -i "$1"
P=/opt/ai-device/packages/0.2.2-native1
"$P/tools/uv" --no-config pip check --python "$P/runtime/bin/python3.13" --offline
useradd -m -u 12345 probe
runuser -u probe -- "$P/runtime/bin/python3.13" -I -B - "$2" <<'PY'
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from device_runtime import native_install, installation

payload = Path('/opt/ai-device/packages/0.2.2-native1')
with zipfile.ZipFile(sys.argv[1]) as wheel:
    assert Path(native_install.__file__).read_bytes() == wheel.read('device_runtime/native_install.py')
print('INSTALLED_NATIVE_INSTALL_MATCHES_WHEEL', flush=True)
root = Path('/home/probe/missing-parent/instance')
real_usage = shutil.disk_usage
try:
    shutil.disk_usage = lambda path: SimpleNamespace(free=0)
    try:
        native_install.install_payload(payload, root=root, instance_id='probe', server_url='http://127.0.0.1:9')
    except installation.InstallationError as exc:
        assert 'insufficient space for bundle copy' in str(exc), str(exc)
    else:
        raise AssertionError('low space accepted')
    assert not root.parent.exists()
    def failed_usage(path):
        raise OSError('injected usage failure')
    shutil.disk_usage = failed_usage
    try:
        native_install.install_payload(payload, root=root, instance_id='probe', server_url='http://127.0.0.1:9')
    except installation.InstallationError as exc:
        assert 'cannot determine available space' in str(exc), str(exc)
    else:
        raise AssertionError('unknown space accepted')
    assert not root.parent.exists()
finally:
    shutil.disk_usage = real_usage
print('LOW_AND_UNKNOWN_SPACE_REFUSED_BEFORE_ROOT', flush=True)
root = Path('/home/probe/clean-instance')
result = native_install.install_payload(payload, root=root, instance_id='probe', server_url='http://127.0.0.1:9')
assert result['instance_root'] == str(root) and (root/'active.json').exists()
active = (root/'active.json').read_bytes()
try:
    native_install.install_payload(payload, root=root, instance_id='probe', server_url='http://127.0.0.1:9')
except installation.InstallationError as exc:
    assert 'refuses existing instances' in str(exc), str(exc)
else:
    raise AssertionError('existing instance unexpectedly replaced')
assert (root/'active.json').read_bytes() == active
print('CLEAN_INSTALL_AND_EXISTING_PROTECTION_OK', flush=True)
PY
