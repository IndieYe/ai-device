#!/bin/sh
# Disposable offline Linux only; inject a recoverable direct-child reap failure.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = supervisor-reap-failure-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 3 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
cp "$3" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import os, signal, socket, subprocess, sys, tempfile, time
from pathlib import Path
from device_runtime import native_bootstrap as b, native_helper as h
with tempfile.TemporaryDirectory(prefix='device-reap-failure-',dir='/root') as root:
    registry=Path(root)/'registry'; registry.mkdir(mode=0o700)
    (registry/'bootstrap-candidates').mkdir(mode=0o700)
    endpoint=Path(root)/'query.sock'; failed=Path(root)/'reap-failed'; release=Path(root)/'allow-reap'; receipt=Path(root)/'reaped'
    # Test-only fixture preparation in the disposable container, never production authorization.
    b._require_proven_candidate_publication_fence=lambda: None
    evidence=b.publish_managed_helper_candidate(registry,Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime'),'candidate')
    code='''from device_runtime import native_bootstrap as b
import os,pathlib,select,signal,sys
supervisor=os.getpid(); real_open=os.pidfd_open; real_close=os.close; real_fork=os.fork; self_fd=[None]; child=[None]
def fork():
    pid=real_fork()
    if pid: child[0]=pid
    return pid
def open_pidfd(pid,*args):
    fd=real_open(pid,*args)
    if pid==supervisor: self_fd[0]=fd
    if sys.argv[8]=='gated' and pid==child[0]:
        signal.pidfd_send_signal(fd,signal.SIGKILL)
        poller=select.poll(); poller.register(fd,select.POLLIN)
        if not poller.poll(2000): raise RuntimeError("gated child did not exit")
        real_close(fd)
        raise OSError("injected child pidfd open failure")
    return fd
def close_fd(fd):
    if os.getpid()==supervisor and fd==self_fd[0]:
        self_fd[0]=None
        real_close(fd)
        raise OSError("injected self pidfd close failure")
    return real_close(fd)
os.fork=fork; os.pidfd_open=open_pidfd; os.close=close_fd
real=b._reap_gated_child_waitpid if sys.argv[8]=='gated' else b._reap_direct_child_pidfd
def injected(fd,*,deadline):
    if not pathlib.Path(sys.argv[6]).exists():
        if sys.argv[8]!='gated':
            try: signal.pidfd_send_signal(fd,signal.SIGKILL)
            except ProcessLookupError: pass
            poller=select.poll(); poller.register(fd,select.POLLIN)
            if not poller.poll(2000): raise RuntimeError("child did not exit")
        pathlib.Path(sys.argv[5]).write_text("failed")
        raise b.BootstrapError("injected unknown reap")
    result=real(fd,deadline=deadline)
    pathlib.Path(sys.argv[7]).write_text(str(fd if sys.argv[8]=='gated' else result[0]))
    return result
if sys.argv[8]=='gated': b._reap_gated_child_waitpid=injected
else: b._reap_direct_child_pidfd=injected
b._supervise_candidate_queries(sys.argv[1],"candidate",registry=sys.argv[2],socket_path=sys.argv[3],group=0,expected_snapshot_sha256=sys.argv[4])
'''
    mode=os.environ.get('DEVICE_REAP_MODE','pidfd')
    assert mode in ('pidfd','gated')
    p=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(registry/'bootstrap-candidates'),str(registry),str(endpoint),evidence['snapshot_sha256'],str(failed),str(release),str(receipt),mode],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        if mode=='pidfd':
            deadline=time.monotonic()+10
            while not endpoint.exists() and p.poll() is None and time.monotonic()<deadline: time.sleep(.02)
            assert endpoint.exists() and p.poll() is None, 'candidate not ready'
            p.send_signal(signal.SIGTERM)
        deadline=time.monotonic()+5
        while not failed.exists() and time.monotonic()<deadline: time.sleep(.02)
        assert failed.exists(), 'failure was not injected'
        p.send_signal(signal.SIGTERM)
        time.sleep(.2)
        assert p.poll() is None, 'supervisor exited while direct child reap remained unknown'
        for lease in (h._daemon_anchor,h._daemon_file_lease):
            try:
                with lease(registry): raise AssertionError('lease released on unknown reap')
            except h.HelperError as exc: assert 'already active' in str(exc)
        try:
            with h.package_lease(registry,exclusive=True): raise AssertionError('package lease released on unknown reap')
        except h.HelperError as exc: assert 'package or start sender still executing' in str(exc)
        release.write_text('allow')
        assert p.wait(timeout=10) != 0, 'unknown cleanup must not report normal stop'
        assert receipt.exists() and int(receipt.read_text()) > 0, 'no direct-child reap receipt'
        if mode=='gated': assert not endpoint.exists(), 'gated child created listener'
        with h._daemon_lease_pair(registry): pass
        with h.package_lease(registry,exclusive=True): pass
        print('REAP_FAILURE_RETAINS_DAEMON_AND_PACKAGE_LOCKS_UNTIL_REAP_CONFIRMED',mode,flush=True)
    finally:
        release.touch()
        if p.poll() is None:
            try: p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(timeout=5)
print('SOURCE_OVERLAY_NOT_DESCENDANT_OR_EXTERNAL_JOB_PROOF')
PY
