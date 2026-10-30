#!/bin/sh
# Disposable offline Linux container only. Never target host processes.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = supervisor-parent-death-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 3 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
cp "$3" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import os, select, signal, socket, struct, subprocess, sys, tempfile, time
from pathlib import Path
from device_runtime import native_bootstrap as b, native_helper as h
with tempfile.TemporaryDirectory(prefix='device-parent-death-',dir='/root') as root:
    registry=Path(root)/'registry'; registry.mkdir(mode=0o700)
    (registry/'bootstrap-candidates').mkdir(mode=0o700)
    endpoint=Path(root)/'query.sock'
    source=Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime')
    evidence=b.publish_managed_helper_candidate(registry,source,'candidate')
    code='from device_runtime import native_bootstrap as b; import sys; b._supervise_candidate_queries(sys.argv[1],"candidate",registry=sys.argv[2],socket_path=sys.argv[3],group=0,expected_snapshot_sha256=sys.argv[4])'
    parent=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(registry/'bootstrap-candidates'),str(registry),str(endpoint),evidence['snapshot_sha256']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    child_fd=None
    try:
        deadline=time.monotonic()+10
        while not endpoint.exists() and parent.poll() is None and time.monotonic()<deadline: time.sleep(.02)
        assert endpoint.exists() and parent.poll() is None, 'supervisor did not start'
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(3); client.connect(str(endpoint))
            child,uid,_=struct.unpack('3i',client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
            assert uid==0 and child!=parent.pid
            child_fd=os.pidfd_open(child)
        # Only terminate the supervisor process created by this fixture.
        parent.kill(); parent.wait(timeout=5)
        assert parent.returncode==-signal.SIGKILL
        poll=select.poll(); poll.register(child_fd,select.POLLIN|select.POLLHUP|select.POLLERR)
        assert poll.poll(4000), 'orphaned query listener survived parent death'
        deadline=time.monotonic()+4
        while True:
            try:
                with h._daemon_lease_pair(registry): break
            except h.HelperError:
                assert time.monotonic()<deadline, 'daemon leases retained after child exit'
                time.sleep(.02)
        print('PARENT_SIGKILL_CHILD_EXIT_AND_LOCKS_RELEASED',flush=True)
    finally:
        if parent.poll() is None: parent.kill(); parent.wait(timeout=5)
        if child_fd is not None:
            poll=select.poll(); poll.register(child_fd,select.POLLIN)
            if not poll.poll(0):
                signal.pidfd_send_signal(child_fd,signal.SIGKILL)
                assert poll.poll(5000), 'fixture child failed to exit'
            os.close(child_fd)
print('SOURCE_OVERLAY_NOT_DESCENDANT_OR_EXTERNAL_JOB_PROOF')
PY
