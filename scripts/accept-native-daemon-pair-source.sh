#!/bin/sh
# Disposable container only. Source overlay experiment, not a package acceptance.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = daemon-pair-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 1e59690850fed8db58fe8c3784fadbd60368afcfb577fbbb67db5f1d59691813 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import os, select, tempfile
from pathlib import Path
from device_runtime import native_helper as h
with tempfile.TemporaryDirectory(prefix='device-pair-', dir='/root') as root:
    registry=Path(root)/'registry'
    registry.mkdir(mode=0o700)
    read_fd,write_fd=os.pipe()
    with h._daemon_lease_pair(registry) as (anchor,lease):
        assert os.fstat(anchor).st_ino==registry.stat().st_ino
        assert os.fstat(lease).st_ino==(registry/'helper-daemon.lock').stat().st_ino
        child=os.fork()
        if child==0:
            os.close(write_fd)
            select.select([read_fd],[],[],3)
            os.close(read_fd)
            os._exit(0)
    os.close(read_fd)
    try:
        for acquire in (h._daemon_anchor,h._daemon_file_lease):
            try:
                with acquire(registry):
                    raise AssertionError('child failed to retain lock')
            except h.HelperError as exc:
                assert 'already active' in str(exc)
    finally:
        os.close(write_fd)
        pid,status=os.waitpid(child,0)
        assert pid==child and os.waitstatus_to_exitcode(status)==0
    with h._daemon_lease_pair(registry):
        pass
print('LINUX_SOURCE_OVERLAY_FORK_PAIR_RETAINED_AND_RELEASED')
print('NOT_SUPERVISION_OR_AUTHORIZATION')
PY
