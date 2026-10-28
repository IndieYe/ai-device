#!/bin/sh
# Disposable offline Linux only. Unwired query supervisor candidate.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = query-supervisor-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 3 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
cp "$3" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import json, os, signal, socket, subprocess, sys, tempfile, time
from pathlib import Path
from device_runtime import native_bootstrap as b, native_helper as h
with tempfile.TemporaryDirectory(prefix='device-supervisor-',dir='/root') as root:
    registry=Path(root)/'registry'; registry.mkdir(mode=0o700)
    (registry/'bootstrap-candidates').mkdir(mode=0o700)
    endpoint=Path(root)/'query.sock'
    source=Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime')
    # Test-only fixture preparation in the disposable container, never production authorization.
    b._require_proven_candidate_publication_fence=lambda: None
    evidence=b.publish_managed_helper_candidate(registry,source,'candidate')
    candidates=registry/'bootstrap-candidates'
    code='from device_runtime import native_bootstrap as b; import sys; print(b._supervise_candidate_queries(sys.argv[1],"candidate",registry=sys.argv[2],socket_path=sys.argv[3],group=0,expected_snapshot_sha256=sys.argv[4]),flush=True)'
    p=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(registry/'bootstrap-candidates'),str(registry),str(endpoint),evidence['snapshot_sha256']],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+10
        while not endpoint.exists() and p.poll() is None and time.monotonic()<deadline: time.sleep(.02)
        if not endpoint.exists() or p.poll() is not None:
            if p.poll() is None: p.send_signal(signal.SIGTERM)
            stdout,stderr=p.communicate(timeout=10)
            raise RuntimeError(f'supervisor not ready rc={p.returncode} stdout={stdout!r} stderr={stderr!r}')
        for lease in (h._daemon_anchor,h._daemon_file_lease):
            try:
                with lease(registry): raise AssertionError('lease absent')
            except h.HelperError as exc: assert 'already active' in str(exc)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            s.settimeout(5); s.connect(str(endpoint)); s.sendall(b'{"action":"start","instance_id":"probe","operation_id":"one"}\n')
            assert json.loads(s.recv(4096))=={'ok':False,'error':'helper_request_refused'}
        p.send_signal(signal.SIGTERM)
        stdout,stderr=p.communicate(timeout=10)
        assert p.returncode==0,(p.returncode,stderr)
        assert b'query_supervisor_stopped_not_authorized' in stdout
        with h._daemon_lease_pair(registry): pass
        intent=registry/'query-supervisor.intent.json'; frozen=intent.read_bytes()
        no_restart=subprocess.run([sys.executable,'-I','-B','-c',code,str(candidates),str(registry),str(Path(root)/'retry.sock'),evidence['snapshot_sha256']],capture_output=True,timeout=10)
        assert no_restart.returncode!=0 and b'intent already exists' in no_restart.stderr
        assert intent.read_bytes()==frozen and not (Path(root)/'retry.sock').exists()
        print('NORMAL_STOP_DOES_NOT_RETIRE_INTENT',flush=True)
        registry=Path(root)/'registry-early'; registry.mkdir(mode=0o700)
        early=Path(root)/'early.sock'; marker=Path(root)/'fork-called'
        early_code='''from device_runtime import native_bootstrap as b
import os,signal,sys
original=b._verified_helper_bytes
fork=os.fork
def verify(*args):
    value=original(*args)
    os.kill(os.getpid(),signal.SIGTERM)
    return value
def guarded_fork():
    open(sys.argv[5],"w").close()
    return fork()
b._verified_helper_bytes=verify
os.fork=guarded_fork
b._supervise_candidate_queries(sys.argv[1],"candidate",registry=sys.argv[2],socket_path=sys.argv[3],group=0,expected_snapshot_sha256=sys.argv[4])
'''
        early_run=subprocess.run([sys.executable,'-I','-B','-c',early_code,str(candidates),str(registry),str(early),evidence['snapshot_sha256'],str(marker)],capture_output=True,timeout=20)
        assert early_run.returncode == -signal.SIGTERM,(early_run.returncode,early_run.stderr)
        assert not early.exists() and not marker.exists()
        with h._daemon_lease_pair(registry): pass
        print('STOP_DURING_VALIDATION_PREVENTS_FORK',flush=True)
        for mode in ('signal-after-fork', 'child-pidfd-failure'):
            registry=Path(root)/('registry-'+mode); registry.mkdir(mode=0o700)
            endpoint2=Path(root)/(mode+'.sock'); receipt=Path(root)/(mode+'.receipt')
            fault_code='''from device_runtime import native_bootstrap as b
import os,signal,sys
real_fork=os.fork
real_pidfd_open=os.pidfd_open
real_waitpid=os.waitpid
real_waitid=os.waitid
child=[None]
def fork():
    pid=real_fork()
    if pid:
        child[0]=pid
        if sys.argv[6]=="signal-after-fork": os.kill(os.getpid(),signal.SIGTERM)
    return pid
def pidfd_open(pid,*args):
    if sys.argv[6]=="child-pidfd-failure" and pid==child[0]: raise OSError("injected child pidfd failure")
    return real_pidfd_open(pid,*args)
def waitpid(pid,flags):
    result=real_waitpid(pid,flags)
    if result[0]==child[0]: open(sys.argv[5],"w").write(str(result[0]))
    return result
def waitid(kind,fd,flags):
    result=real_waitid(kind,fd,flags)
    if result is not None and not flags & os.WNOWAIT: open(sys.argv[5],"w").write(str(result.si_pid))
    return result
os.fork=fork
os.pidfd_open=pidfd_open
os.waitpid=waitpid
os.waitid=waitid
try:
    b._supervise_candidate_queries(sys.argv[1],"candidate",registry=sys.argv[2],socket_path=sys.argv[3],group=0,expected_snapshot_sha256=sys.argv[4])
except b.BootstrapError:
    if child[0] is not None:
        try: real_waitpid(child[0],os.WNOHANG)
        except ChildProcessError: open(sys.argv[5],"w").write(str(child[0]))
    raise
'''
            result=subprocess.run([sys.executable,'-I','-B','-c',fault_code,str(candidates),str(registry),str(endpoint2),evidence['snapshot_sha256'],str(receipt),mode],capture_output=True,timeout=20)
            assert result.returncode != 0,(mode,result.stdout,result.stderr)
            assert receipt.exists() and int(receipt.read_text())>0,(mode,result.returncode,result.stderr)
            assert not endpoint2.exists(),mode
            with h._daemon_lease_pair(registry): pass
            print('FORK_PIDFD_WINDOW_REAPED_'+mode.upper(),flush=True)
    finally:
        if p.poll() is None:
            p.kill(); p.wait(timeout=5)
print('QUERY_SUPERVISOR_TERM_CHILD_REAP_AND_LOCK_RELEASE_OK')
print('SOURCE_OVERLAY_NO_PARENT_SIGKILL_OR_DESCENDANT_GUARANTEE')
PY
