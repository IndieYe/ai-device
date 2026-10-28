#!/bin/sh
# Disposable Linux fixture: actual query supervisor and actual Systemd.query with a substitute worker.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = query-supervisor-descendant-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 3 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
cp "$3" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import os,select,signal,subprocess,sys,tempfile,time
from pathlib import Path
from device_runtime import native_bootstrap as b, native_helper as h
with tempfile.TemporaryDirectory(prefix='device-query-supervisor-tree-',dir='/root') as tmp:
    root=Path(tmp); registry=root/'registry'; registry.mkdir(mode=0o700)
    (registry/'bootstrap-candidates').mkdir(mode=0o700)
    endpoint=root/'unused.sock'; ready=root/'worker-pid'; release=root/'release'; child=root/'handler-pid'; fake=root/'fake-systemctl'
    fake.write_text('#!'+sys.executable+'\nimport os,time,pathlib\np=pathlib.Path('+repr(str(root))+')\n(p/"worker-pid").write_text(str(os.getpid()))\ndeadline=time.monotonic()+20\nwhile not (p/"release").exists() and time.monotonic()<deadline: time.sleep(.05)\n')
    fake.chmod(0o700)
    # Test-only fixture preparation in the disposable container, never production authorization.
    b._require_proven_candidate_publication_fence=lambda: None
    evidence=b.publish_managed_helper_candidate(registry,Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime'),'candidate')
    candidates=registry/'bootstrap-candidates'
    preflight='''import subprocess,sys
from device_runtime import native_bootstrap as b
p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(10)"])
try:
 try:
  with b._exclusive_query_subreaper(): pass
 except b.BootstrapError as exc:
  assert "existing children" in str(exc)
 else: raise AssertionError("existing child accepted")
 assert p.poll() is None
finally:
 p.terminate(); p.wait(timeout=5)
'''
    subprocess.run([sys.executable,'-I','-B','-c',preflight],check=True,timeout=10)
    print('SUPERVISOR_REJECTS_PREEXISTING_CHILD_WITHOUT_REAPING')
    blocked_marker=root/'blocked-validation'; blocked_endpoint=root/'blocked.sock'
    blocked_code='''import pathlib,sys
from device_runtime import native_bootstrap as b
def verify(*args): pathlib.Path(sys.argv[5]).write_text("unexpected"); raise AssertionError("verification ran")
b._verified_helper_bytes=verify
b._supervise_candidate_queries(sys.argv[1],"candidate",registry=sys.argv[2],socket_path=sys.argv[3],group=0,expected_snapshot_sha256=sys.argv[4])
'''
    with h.package_lease(registry,exclusive=True):
        blocked=subprocess.run([sys.executable,'-I','-B','-c',blocked_code,str(registry/'bootstrap-candidates'),str(registry),str(blocked_endpoint),evidence['snapshot_sha256'],str(blocked_marker)],capture_output=True,timeout=10)
        assert blocked.returncode!=0 and b'package or start sender still executing' in blocked.stderr
        assert not blocked_marker.exists() and not blocked_endpoint.exists()
    with h._daemon_lease_pair(registry): pass
    print('PACKAGE_EX_BLOCKS_SUPERVISOR_BEFORE_VERIFICATION')
    for failure in ('file','directory'):
        failed_registry=root/('registry-fsync-'+failure); failed_registry.mkdir(mode=0o700)
        failed_marker=root/('failed-validation-'+failure); failed_socket=root/('failed-'+failure+'.sock')
        failed_code='''import os,pathlib,stat,sys
from device_runtime import native_bootstrap as b
real=os.fsync
def fsync(fd):
 is_dir=stat.S_ISDIR(os.fstat(fd).st_mode)
 if is_dir==(sys.argv[6]=="directory"): raise OSError("injected fsync failure")
 return real(fd)
def verify(*args): pathlib.Path(sys.argv[5]).write_text("unexpected"); raise AssertionError("verification ran")
os.fsync=fsync; b._verified_helper_bytes=verify
b._supervise_candidate_queries(sys.argv[1],"candidate",registry=sys.argv[2],socket_path=sys.argv[3],group=0,expected_snapshot_sha256=sys.argv[4])
'''
        failed=subprocess.run([sys.executable,'-I','-B','-c',failed_code,str(candidates),str(failed_registry),str(failed_socket),evidence['snapshot_sha256'],str(failed_marker),failure],capture_output=True,timeout=10)
        assert failed.returncode!=0 and b'intent publication failed or unknown' in failed.stderr
        assert not failed_marker.exists() and not failed_socket.exists()
        intent=failed_registry/'query-supervisor.intent.json'
        assert intent.exists()==(failure=='directory')
        if intent.exists():
            before=intent.read_bytes()
            retry=subprocess.run([sys.executable,'-I','-B','-c',blocked_code,str(candidates),str(failed_registry),str(failed_socket),evidence['snapshot_sha256'],str(failed_marker)],capture_output=True,timeout=10)
            assert retry.returncode!=0 and b'intent already exists' in retry.stderr
            assert intent.read_bytes()==before and not failed_marker.exists()
        with h._daemon_lease_pair(failed_registry): pass
        with h.package_lease(failed_registry,exclusive=True): pass
        print('INTENT_FSYNC_FAILURE_BLOCKS_VALIDATION',failure)
    vready=root/'validation-worker-pid'; vrelease=root/'validation-release'; vdone=root/'validation-returned'; vendpoint=root/'validation.sock'
    validation_code='''import os,pathlib,subprocess,sys,time
from device_runtime import native_bootstrap as b
real=b._verified_helper_bytes
def verify(*args):
 code="import os,pathlib,time; p=pathlib.Path("+repr(sys.argv[5])+"); r=pathlib.Path("+repr(sys.argv[6])+"); p.write_text(str(os.getpid())); end=time.monotonic()+20\\nwhile not r.exists() and time.monotonic()<end: time.sleep(.05)"
 subprocess.Popen([sys.executable,"-I","-B","-c",code],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 result=real(*args)
 pathlib.Path(sys.argv[7]).write_text("verified")
 if sys.argv[8]=='fail': raise b.BootstrapError('injected validation failure')
 return result
b._verified_helper_bytes=verify
b._supervise_candidate_queries(sys.argv[1],"candidate",registry=sys.argv[2],socket_path=sys.argv[3],group=0,expected_snapshot_sha256=sys.argv[4])
'''
    verror=(root/'validation-stderr').open('w+')
    vmode=os.environ.get('DEVICE_VALIDATION_MODE','success')
    assert vmode in ('success','fail')
    vp=subprocess.Popen([sys.executable,'-I','-B','-c',validation_code,str(registry/'bootstrap-candidates'),str(registry),str(vendpoint),evidence['snapshot_sha256'],str(vready),str(vrelease),str(vdone),vmode],stdout=subprocess.DEVNULL,stderr=verror)
    vfd=None
    try:
        end=time.monotonic()+12
        while not (vready.exists() and vdone.exists()) and vp.poll() is None and time.monotonic()<end: time.sleep(.02)
        verror.flush(); verror.seek(0)
        assert vready.exists() and vdone.exists() and vp.poll() is None, ('validation worker not ready',verror.read())
        vfd=os.pidfd_open(int(vready.read_text()))
        assert not vendpoint.exists(), 'handler released before verification descendant reaped'
        for lease in (h._daemon_anchor,h._daemon_file_lease):
            try:
                with lease(registry): raise AssertionError('lease released while validation worker alive')
            except h.HelperError as exc: assert 'already active' in str(exc)
        vrelease.touch()
        poll=select.poll(); poll.register(vfd,select.POLLIN); assert poll.poll(5000)
        if vmode=='success':
            end=time.monotonic()+8
            while not vendpoint.exists() and vp.poll() is None and time.monotonic()<end: time.sleep(.02)
            assert vendpoint.exists() and vp.poll() is None, 'handler not released after validation worker'
            vp.send_signal(signal.SIGTERM); assert vp.wait(timeout=10)==0
        else:
            assert vp.wait(timeout=10)!=0 and not vendpoint.exists(), 'failed validation started handler'
        with h._daemon_lease_pair(registry): pass
        print('VALIDATION_DESCENDANT_REAPED_BEFORE_HANDLER_RELEASE',vmode)
    finally:
        vrelease.touch()
        if vp.poll() is None:
            try: vp.wait(timeout=8)
            except subprocess.TimeoutExpired: vp.kill(); vp.wait(timeout=5)
        if vfd is not None:
            poll=select.poll(); poll.register(vfd,select.POLLIN)
            if not poll.poll(5000): signal.pidfd_send_signal(vfd,signal.SIGKILL); assert poll.poll(5000)
            os.close(vfd)
        verror.close()
    registry=root/'registry-query'; registry.mkdir(mode=0o700)
    code='''import contextlib,os,pathlib,sys
from device_runtime import native_bootstrap as b
real_fork=os.fork
real_modules=b._verified_helper_modules
def fork():
 pid=real_fork()
 if pid: pathlib.Path(sys.argv[6]).write_text(str(pid))
 return pid
@contextlib.contextmanager
def modules(contents):
 with real_modules(contents) as candidate:
  def query_fixture(*args,**kwargs): candidate.Systemd(executable=sys.argv[5]).query("fixture.service")
  candidate._serve_credential_queries_locked=query_fixture
  yield candidate
os.fork=fork
b._verified_helper_modules=modules
b._supervise_candidate_queries(sys.argv[1],"candidate",registry=sys.argv[2],socket_path=sys.argv[3],group=0,expected_snapshot_sha256=sys.argv[4])
'''
    p=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(candidates),str(registry),str(endpoint),evidence['snapshot_sha256'],str(fake),str(child)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    worker_fd=None; handler_fd=None
    try:
        end=time.monotonic()+10
        while not ready.exists() and p.poll() is None and time.monotonic()<end: time.sleep(.02)
        assert ready.exists() and child.exists() and p.poll() is None, 'supervised query fixture not ready'
        worker_fd=os.pidfd_open(int(ready.read_text())); handler_fd=os.pidfd_open(int(child.read_text()))
        p.send_signal(signal.SIGTERM)
        hp=select.poll(); hp.register(handler_fd,select.POLLIN)
        assert hp.poll(5000), 'handler did not exit'
        wp=select.poll(); wp.register(worker_fd,select.POLLIN)
        assert not wp.poll(0), 'query worker did not remain active'
        time.sleep(.2); assert p.poll() is None, 'supervisor exited with adopted worker alive'
        for lease in (h._daemon_anchor,h._daemon_file_lease):
            try:
                with lease(registry): raise AssertionError('lease released while worker alive')
            except h.HelperError as exc: assert 'already active' in str(exc)
        release.touch()
        assert wp.poll(5000), 'worker did not exit'
        assert p.wait(timeout=10)==0, 'supervisor did not finish after adopted worker exited'
        with h._daemon_lease_pair(registry): pass
        print('ACTUAL_QUERY_SUPERVISOR_HOLDS_LOCKS_UNTIL_ADOPTED_QUERY_WORKER_REAPED')
    finally:
        release.touch()
        if p.poll() is None:
            try: p.wait(timeout=8)
            except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=5)
        for fd in (worker_fd,handler_fd):
            if fd is not None:
                poll=select.poll(); poll.register(fd,select.POLLIN)
                if not poll.poll(5000):
                    signal.pidfd_send_signal(fd,signal.SIGKILL)
                    assert poll.poll(5000)
                os.close(fd)
print('FIXTURE_QUERY_LOOP_OVERRIDE_NOT_PRODUCTION_HANDLER_OR_EXTERNAL_JOB')
PY
