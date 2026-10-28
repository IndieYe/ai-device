#!/bin/sh
# Offline disposable container. Actual private listener and Helper.handle; fake systemctl only.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = query-handler-tree-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 3 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
P=/opt/ai-device/packages/0.2.2-native1
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
cp "$3" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
useradd -m -u 12345 probe
runuser -u probe -- "$P/runtime/bin/python3.13" -I -B -m device_runtime.native_install --payload "$P" --root /home/probe/instance --instance-id probe --server-url http://127.0.0.1:9
mkdir -p /var/lib/ai-device/manager /etc/systemd/system
printf 'TEST_ONLY=yes\n' > /etc/device-query-fixture.env
chmod 600 /etc/device-query-fixture.env
"$B/bin/python3.13" -I -B - <<'PY'
import json,os,select,signal,socket,subprocess,sys,tempfile,time
from pathlib import Path
from device_runtime import native_bootstrap as b, native_helper as h
registry=Path('/var/lib/ai-device/manager')
h.register(registry,Path('/etc/systemd/system'),instance_id='probe',root=Path('/home/probe/instance'),username='probe',environment_file='/etc/device-query-fixture.env')
(registry/'bootstrap-candidates').mkdir(mode=0o700)
evidence=b.publish_managed_helper_candidate(registry,Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime'),'candidate')
with tempfile.TemporaryDirectory(prefix='device-real-query-tree-',dir='/root') as tmp:
 root=Path(tmp); endpoint=root/'query.sock'; ready=root/'worker-pid'; grand=root/'grand-pid'; release=root/'release'; handler=root/'handler-pid'; fake=root/'fake-systemctl'; failed=root/'wait-failed'; allow=root/'allow-wait'
 fake.write_text('#!'+sys.executable+'\nimport os,time,pathlib\np=pathlib.Path('+repr(str(root))+')\nc=os.fork()\nif c==0:\n (p/"grand-pid").write_text(str(os.getpid()))\nelse:\n (p/"worker-pid").write_text(str(os.getpid()))\nend=time.monotonic()+25\nwhile not (p/"release").exists() and time.monotonic()<end: time.sleep(.05)\nif c: os.waitpid(c,0)\n')
 fake.chmod(0o700)
 code='''import contextlib,os,pathlib,sys
from device_runtime import native_bootstrap as b
real_fork=os.fork; real_modules=b._verified_helper_modules; real_wait=b._wait_adopted_query_children
def fork():
 pid=real_fork()
 if pid: pathlib.Path(sys.argv[6]).write_text(str(pid))
 return pid
@contextlib.contextmanager
def modules(contents):
 with real_modules(contents) as candidate:
  original=candidate.Systemd.__init__
  def init(self,executable="/usr/bin/systemctl"): original(self,executable=sys.argv[5])
  candidate.Systemd.__init__=init
  yield candidate
def wait_children():
 if pathlib.Path(sys.argv[9]).exists() and not pathlib.Path(sys.argv[8]).exists():
  pathlib.Path(sys.argv[7]).write_text("unknown")
  raise OSError("injected adopted wait failure")
 return real_wait()
os.fork=fork; b._verified_helper_modules=modules; b._wait_adopted_query_children=wait_children
b._supervise_candidate_queries(sys.argv[1],"candidate",registry=sys.argv[2],socket_path=sys.argv[3],group=0,expected_snapshot_sha256=sys.argv[4])
'''
 p=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(registry/'bootstrap-candidates'),str(registry),str(endpoint),evidence['snapshot_sha256'],str(fake),str(handler),str(failed),str(allow),str(ready)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 client=None; fds=[]
 try:
  end=time.monotonic()+12
  while not endpoint.exists() and p.poll() is None and time.monotonic()<end: time.sleep(.02)
  assert endpoint.exists() and p.poll() is None, 'listener not ready'
  client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); client.settimeout(5); client.connect(str(endpoint))
  client.sendall(b'{"action":"query","instance_id":"probe","operation_id":"one"}\n')
  end=time.monotonic()+8
  while not (ready.exists() and grand.exists()) and p.poll() is None and time.monotonic()<end: time.sleep(.02)
  assert ready.exists() and grand.exists(), 'real query did not start test worker tree'
  fds=[os.pidfd_open(int(path.read_text())) for path in (handler,ready,grand)]
  mode=os.environ.get('DEVICE_SUPERVISOR_MODE','unknown-wait')
  assert mode in ('unknown-wait','crash')
  p.send_signal(signal.SIGKILL if mode=='crash' else signal.SIGTERM)
  poll=select.poll(); poll.register(fds[0],select.POLLIN); assert poll.poll(5000),'handler still running'
  if mode=='crash':
   assert p.wait(timeout=5)==-signal.SIGKILL
  else:
   end=time.monotonic()+5
   while not failed.exists() and p.poll() is None and time.monotonic()<end: time.sleep(.02)
   assert failed.exists(), 'adopted wait error not injected'
  for fd in fds[1:]:
   poll=select.poll(); poll.register(fd,select.POLLIN); assert not poll.poll(0),'worker died prematurely'
  if mode=='crash':
   with h._daemon_lease_pair(registry): pass
   print('SUPERVISOR_SIGKILL_RELEASES_LOCKS_WHILE_QUERY_DESCENDANTS_LIVE')
  else:
   assert p.poll() is None, 'supervisor exited with live descendants'
   for lease in (h._daemon_anchor,h._daemon_file_lease):
    try:
     with lease(registry): raise AssertionError('lease missing during handler descendants')
    except h.HelperError as exc: assert 'already active' in str(exc)
  release.touch()
  for fd in fds[1:]:
   poll=select.poll(); poll.register(fd,select.POLLIN); assert poll.poll(5000),'worker did not exit'
  if mode=='unknown-wait':
   assert p.poll() is None, 'unknown adopted wait released leases'
   for lease in (h._daemon_anchor,h._daemon_file_lease):
    try:
     with lease(registry): raise AssertionError('lease released on unknown adopted wait')
    except h.HelperError as exc: assert 'already active' in str(exc)
   allow.touch()
   assert p.wait(timeout=10)!=0, 'unknown wait reported normal stop'
  with h._daemon_lease_pair(registry): pass
  assert not list(registry.glob('op-probe-*.json'))
  if mode=='unknown-wait': print('REAL_QUERY_HANDLER_MULTIGENERATION_UNKNOWN_WAIT_HOLDS_LEASE_UNTIL_REAPED')
 finally:
  release.touch()
  allow.touch()
  if client is not None: client.close()
  if p.poll() is None:
   try: p.wait(timeout=8)
   except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=5)
  for fd in fds:
   poll=select.poll(); poll.register(fd,select.POLLIN)
   if not poll.poll(5000): signal.pidfd_send_signal(fd,signal.SIGKILL); assert poll.poll(5000)
   os.close(fd)
print('FAKE_SYSTEMCTL_NO_REAL_DEVICE_SERVICE_OR_EXTERNAL_JOB_PROOF')
PY
