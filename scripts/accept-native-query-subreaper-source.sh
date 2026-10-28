#!/bin/sh
# Disposable offline Linux only; observation fixture, not production supervision.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = query-subreaper-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import ctypes, os, select, signal, subprocess, sys, tempfile, time
from pathlib import Path
from device_runtime import native_helper as h
libc=ctypes.CDLL(None,use_errno=True)
assert libc.prctl(36,1,0,0,0)==0, 'set subreaper failed'
flag=ctypes.c_int()
assert libc.prctl(37,ctypes.byref(flag),0,0,0)==0 and flag.value==1, 'get subreaper failed'
with tempfile.TemporaryDirectory(prefix='device-query-subreaper-',dir='/root') as root:
    root=Path(root); registry=root/'registry'; registry.mkdir(mode=0o700)
    ready=root/'worker-pid'; release=root/'release'; fake=root/'fake-systemctl'
    fake.write_text('#!'+sys.executable+'\nimport os,time,pathlib\np=pathlib.Path('+repr(str(root))+')\n(p/"worker-pid").write_text(str(os.getpid()))\ndeadline=time.monotonic()+15\nwhile not (p/"release").exists() and time.monotonic()<deadline: time.sleep(.05)\n')
    fake.chmod(0o700)
    code='from device_runtime import native_helper as h; import sys; h.Systemd(executable=sys.argv[1]).query("fixture.service")'
    worker_fd=None; handler=None
    with h._daemon_lease_pair(registry):
      try:
        handler=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(fake)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+10
        while not ready.exists() and handler.poll() is None and time.monotonic()<deadline: time.sleep(.02)
        assert ready.exists() and handler.poll() is None, 'worker not ready'
        worker=int(ready.read_text()); worker_fd=os.pidfd_open(worker)
        pfd=os.pidfd_open(handler.pid)
        try: signal.pidfd_send_signal(pfd,signal.SIGKILL)
        finally: os.close(pfd)
        handler.wait(timeout=5)
        waited,_=os.waitpid(-1,os.WNOHANG)
        assert waited==0, 'expected live adopted worker, not ECHILD or a reaped process'
        poller=select.poll(); poller.register(worker_fd,select.POLLIN)
        assert not poller.poll(0), 'worker already exited'
        contender=subprocess.run([sys.executable,'-I','-B','-c',
            'from device_runtime import native_helper as h; import sys\ntry:\n with h._daemon_lease_pair(sys.argv[1]): sys.exit(0)\nexcept h.HelperError: sys.exit(3)',str(registry)],timeout=5)
        assert contender.returncode==3, 'supervisor did not hold leases'
        release.touch()
        assert poller.poll(5000), 'worker did not finish'
        waited,_=os.waitpid(-1,0)
        assert waited==worker, 'unexpected adopted child'
        try: os.waitpid(-1,os.WNOHANG)
        except ChildProcessError: pass
        else: raise AssertionError('ECHILD not reached')
        print('SUBREAPER_ADOPTS_QUERY_WORKER_AND_HOLDS_LEASES_UNTIL_REAP')
      finally:
        release.touch()
        if handler is not None and handler.poll() is None:
            handler.kill(); handler.wait(timeout=5)
        if worker_fd is not None:
            poller=select.poll(); poller.register(worker_fd,select.POLLIN)
            if not poller.poll(5000):
                signal.pidfd_send_signal(worker_fd,signal.SIGKILL)
                assert poller.poll(5000)
            os.close(worker_fd)
            try: os.waitpid(-1,os.WNOHANG)
            except ChildProcessError: pass
    with h._daemon_lease_pair(registry): pass
print('FIXTURE_ONLY_NO_TREE_KILL_OR_EXTERNAL_JOB_PROOF')
PY
