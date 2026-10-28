#!/bin/sh
# Disposable offline container only; exact dbd1a20 package, not production.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = daemon-pair-dbd1a20 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' b700bcf4289ec853804db6d46c8d8ab3d0dff7964195adaba2fddb82abfdbd0f "$1" | sha256sum -c -
printf '%s  %s\n' 8f4c66ef643804a47bf1705ec3f8d8f8fac9026abe4be3fdb570cc2976d051e0 "$2" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
P=/opt/ai-device/packages/0.2.2-native1
"$P/tools/uv" --no-config pip check --python "$P/runtime/bin/python3.13" --offline
"$B/bin/python3.13" -I -B - "$2" <<'PY'
import os, select, sys, tempfile, zipfile
from pathlib import Path
from device_runtime import native_helper as h
with zipfile.ZipFile(sys.argv[1]) as wheel:
    assert Path(h.__file__).read_bytes() == wheel.read('device_runtime/native_helper.py')
with tempfile.TemporaryDirectory(prefix='device-pair-', dir='/root') as root:
    registry = Path(root) / 'registry'
    registry.mkdir(mode=0o700)
    read_fd, write_fd = os.pipe()
    with h._daemon_lease_pair(registry) as (anchor, lease):
        assert os.fstat(anchor).st_ino == registry.stat().st_ino
        assert os.fstat(lease).st_ino == (registry / 'helper-daemon.lock').stat().st_ino
        child = os.fork()
        if child == 0:
            os.close(write_fd)
            select.select([read_fd], [], [], 3)
            os.close(read_fd)
            os._exit(0)
    os.close(read_fd)
    try:
        for acquire in (h._daemon_anchor, h._daemon_file_lease):
            try:
                with acquire(registry):
                    raise AssertionError('child failed to retain lock')
            except h.HelperError as exc:
                assert 'already active' in str(exc)
    finally:
        os.close(write_fd)
        pid, status = os.waitpid(child, 0)
        assert pid == child and os.waitstatus_to_exitcode(status) == 0
    with h._daemon_lease_pair(registry):
        pass
print('EXACT_PACKAGE_FORK_PAIR_RETAINED_AND_RELEASED')
print('NOT_SUPERVISION_OR_AUTHORIZATION')
PY
