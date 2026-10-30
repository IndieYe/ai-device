#!/bin/sh
# Disposable offline Linux only; no real systemctl or Device service.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = query-subprocess-orphan-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import os, select, signal, socket, subprocess, sys, tempfile, time
from pathlib import Path
from device_runtime import native_helper as h
with tempfile.TemporaryDirectory(prefix='device-query-orphan-',dir='/root') as root:
    root=Path(root); registry=root/'registry'; registry.mkdir(mode=0o700)
    endpoint=root/'query.sock'; ready=root/'worker-pid'; release=root/'release'; fds=root/'fds'
    fake=root/'fake-systemctl'
    fake.write_text('#!'+sys.executable+'\nimport os,time,pathlib\np=pathlib.Path('+repr(str(root))+')\nlinks=[]\nfor fd in pathlib.Path("/proc/self/fd").iterdir():\n try: links.append(os.readlink(fd))\n except FileNotFoundError: pass\n(p/"fds").write_text("\\n".join(links))\n(p/"worker-pid").write_text(str(os.getpid()))\ndeadline=time.monotonic()+15\nwhile not (p/"release").exists() and time.monotonic()<deadline: time.sleep(.05)\n')
    fake.chmod(0o700)
    code='''import socket,sys
from device_runtime import native_helper as h
with h._daemon_lease_pair(sys.argv[1]):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
  listener.bind(sys.argv[2]); listener.listen(1)
  h.Systemd(executable=sys.argv[3]).query("fixture.service")
'''
    p=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(registry),str(endpoint),str(fake)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    fd=None
    try:
        deadline=time.monotonic()+10
        while not ready.exists() and p.poll() is None and time.monotonic()<deadline: time.sleep(.02)
        assert ready.exists() and p.poll() is None, 'fixture subprocess not ready'
        worker=int(ready.read_text()); fd=os.pidfd_open(worker)
        poller=select.poll(); poller.register(fd,select.POLLIN)
        assert not poller.poll(0), 'worker already exited'
        parent_fd=os.pidfd_open(p.pid)
        try: signal.pidfd_send_signal(parent_fd,signal.SIGKILL)
        finally: os.close(parent_fd)
        p.wait(timeout=5)
        assert not poller.poll(0), 'worker did not survive handler exit'
        with h._daemon_lease_pair(registry): pass
        assert not any(x.startswith('socket:[') for x in fds.read_text().splitlines()), 'worker inherited socket'
        print('QUERY_WORKER_SURVIVES_HANDLER_EXIT_AFTER_LEASE_RELEASE')
        print('FIXTURE_WORKER_NO_INHERITED_SOCKET')
    finally:
        release.touch()
        if p.poll() is None: p.kill(); p.wait(timeout=5)
        if fd is not None:
            poller=select.poll(); poller.register(fd,select.POLLIN)
            if not poller.poll(5000):
                signal.pidfd_send_signal(fd,signal.SIGKILL)
                assert poller.poll(5000), 'fixture worker did not exit'
            os.close(fd)
print('FIXTURE_ONLY_NOT_EXTERNAL_JOB_OR_PRODUCTION_PROOF')
PY
