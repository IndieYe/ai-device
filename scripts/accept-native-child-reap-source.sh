#!/bin/sh
# Disposable offline Linux container only; source overlay experiment.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = child-reap-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
"$B/bin/python3.13" -I -B - <<'PY'
import os, select, signal, tempfile, time
from pathlib import Path
from device_runtime import native_bootstrap as b, native_helper as h
self_fd=os.pidfd_open(os.getpid())
try:
    try:
        b._reap_direct_child_pidfd(self_fd,deadline=time.monotonic()+3)
        raise AssertionError('nonchild accepted')
    except b.BootstrapError as exc:
        assert 'direct child' in str(exc)
finally:
    os.close(self_fd)
with tempfile.TemporaryDirectory(prefix='device-reap-',dir='/root') as root:
    registry=Path(root)/'registry'; registry.mkdir(mode=0o700)
    with h._daemon_lease_pair(registry):
        child=os.fork()
        if child==0:
            time.sleep(30)
            os._exit(3)
        fd=os.pidfd_open(child)
        try:
            for acquire in (h._daemon_anchor,h._daemon_file_lease):
                try:
                    with acquire(registry): raise AssertionError('lock not held')
                except h.HelperError as exc: assert 'already active' in str(exc)
            pid,code,status=b._reap_direct_child_pidfd(fd,deadline=time.monotonic()+5)
            assert (pid,code,status)==(child,os.CLD_KILLED,signal.SIGKILL)
            try: os.waitpid(child,os.WNOHANG)
            except ChildProcessError: pass
            else: raise AssertionError('child not reaped')
            for acquire in (h._daemon_anchor,h._daemon_file_lease):
                try:
                    with acquire(registry): raise AssertionError('parent released lock too early')
                except h.HelperError as exc: assert 'already active' in str(exc)
        finally:
            os.close(fd)
    with h._daemon_lease_pair(registry): pass
    child=os.fork()
    if child==0: os._exit(7)
    fd=os.pidfd_open(child)
    try:
        poller=select.poll(); poller.register(fd,select.POLLIN)
        assert poller.poll(5000)
        assert b._reap_direct_child_pidfd(fd,deadline=time.monotonic()+5)==(child,os.CLD_EXITED,7)
    finally: os.close(fd)
print('PIDFD_DIRECT_CHILD_LIVE_AND_EXITED_REAPED_NONCHILD_REFUSED')
print('SOURCE_OVERLAY_NOT_DESCENDANT_REAP_OR_AUTHORIZATION')
PY
