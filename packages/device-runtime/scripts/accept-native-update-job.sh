#!/bin/sh
# Disposable systemd Linux container only. Real Device preinst must reject apply.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = job-v1 ] || exit 2
[ "$(cat /proc/1/comm)" = systemd ] && [ "$(id -u)" = 0 ] || exit 2
[ "$#" = 2 ] || { echo 'usage: SCRIPT FULL_MANAGER_DEB CURRENT_WHEELS' >&2; exit 2; }
[ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
# Install an explicit inert, older source-package fixture into this disposable
# dpkg database; the actual Device payload is extracted, not installed. This
# preserves a distinct source version while testing the real candidate preinst.
mkdir -p /tmp/device-source-fixture/DEBIAN
cat > /tmp/device-source-fixture/DEBIAN/control <<'EOF'
Package: ai-device-native-client
Version: 0.2.1-1
Architecture: amd64
Maintainer: Disposable test fixture
Description: inert source package for isolated job reconciliation test
EOF
dpkg-deb --build /tmp/device-source-fixture /tmp/device-source-fixture.deb >/dev/null
dpkg --install /tmp/device-source-fixture.deb
dpkg-deb -x "$1" /
P=/opt/ai-device/packages/0.2.2-native1
export PYTHONDONTWRITEBYTECODE=1
$P/runtime/bin/python3.13 -I -B -m pip --isolated install --no-index --no-deps --force-reinstall "$2/"*.whl
useradd -m -u 12345 probe
runuser -u probe -- "$P/runtime/bin/python3.13" -I -B -m device_runtime.native_install --payload "$P" --root /home/probe/instance --instance-id probe --server-url http://127.0.0.1:9
mkdir -p /var/lib/ai-device/manager /var/lib/ai-device/inputs
cp "$1" /var/lib/ai-device/inputs/candidate.deb
printf 'TEST_ONLY=yes\n' > /etc/device-job-fixture.env
chmod 600 /etc/device-job-fixture.env
$P/runtime/bin/python3.13 -I -B - <<'PY'
import json,time,subprocess,os,sys
from pathlib import Path
from device_runtime import native_helper as helper
from device_runtime import native_update as update
from device_runtime import native_update_job as job
if os.environ.get('DEVICE_JOB_FAULT') in ('start-lock','start-guard-crash','start-daemon-crash','start-daemon-stop','start-guard-pause'):
 original_render=helper.render_client_unit
 def delayed_unit(**kwargs):
  return original_render(**kwargs).replace('[Service]\n','[Service]\nExecStartPre=/usr/bin/sleep 8\n')
 helper.render_client_unit=delayed_unit
registry=Path('/var/lib/ai-device/manager')
root=Path('/home/probe/instance')
package=Path('/var/lib/ai-device/inputs/candidate.deb')
if os.environ.get('DEVICE_JOB_FAULT') in ('start-daemon-stop','start-guard-pause'):
 # The disposable runner is installed before registration so its exact bytes
 # are bound in the normal registry and unit checks; finite, no network.
 (root/'run').write_text(f'#!{sys.executable}\nimport time\ntime.sleep(90)\n')
helper.register(registry,Path('/etc/systemd/system'),instance_id='probe',root=root,username='probe',environment_file='/etc/device-job-fixture.env')
subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True)
manager=update.Updates(registry)
prepared=manager.prepare('probe','job-one',package,sha256=helper._digest(package),version='0.2.2-1')
stem=registry/'updates/5-probe--job-one'
manager.approve('probe','job-one',prepared_sha256=helper._digest(stem.with_suffix('.prepared.json')))
active=(root/'active.json').read_bytes()
def diagnostic(expected):
 def snapshot():
  entries={}
  for path in registry.rglob('*'):
   info=path.lstat()
   entries[str(path.relative_to(registry))]=(info.st_mode,info.st_mtime_ns,helper._digest(path) if path.is_file() else None)
  return entries
 before=snapshot()
 report=job.observe_job(manager,'probe','job-one')
 assert report['state']==expected,report
 assert report['retry_allowed'] is False and report['fence_retained']
 assert snapshot()==before,'diagnostic changed persistent files/directories'
 return report
mode=os.environ.get('DEVICE_JOB_FAULT','none')
assert mode in ('none','lost-response','source-drift','close-before-apply','retire-retry','start-lock','start-guard-crash','start-daemon-crash','start-daemon-stop','start-guard-pause')
calls=[]
real_run=subprocess.run
def runner(argv,**kwargs):
 calls.append(argv)
 if mode in ('source-drift','close-before-apply','retire-retry'):
  # Test-only bounded queue delay; production dispatch has no arbitrary properties.
  argv=[argv[0],'--no-block','--property=ExecStartPre=/usr/bin/sleep 8','--property=StandardError=file:/var/lib/ai-device/worker-test-error.log',*argv[1:]]
 result=real_run(argv,**kwargs)
 if mode=='lost-response':
  raise subprocess.TimeoutExpired(argv,30)
 return result
# First test the unmodified production fence before any intent or runner call.
try:
 job.dispatch_job(manager,'probe','job-one',runner=runner)
except job.JobError as error:
 assert 'new dispatch disabled' in str(error),error
else:
 raise AssertionError('production dispatch fence unexpectedly allowed a new job')
assert not stem.with_suffix('.job-intent.json').exists() and not calls
print(json.dumps({'state':'production_dispatch_fence_refused','runner_calls':0,'intent_created':False}))
if mode in ('start-lock','start-guard-crash','start-daemon-crash','start-daemon-stop','start-guard-pause'):
 import threading
 runtime=Path('/run/device-helper-acceptance'); runtime.mkdir(mode=0o755)
 socket_path=runtime/'helper.sock'
 unit=Path('/etc/systemd/system/ai-device-native-helper.service')
 unit_content=helper.render_helper_unit(bootstrap_python=Path(sys.executable),registry=registry,socket_path=socket_path,group=0)
 if mode in ('start-daemon-crash','start-daemon-stop','start-guard-pause'):
  assert 'KillMode=process\n' in unit_content,'product unit must preserve sender guard'
 unit.write_text(unit_content)
 real_run(['/usr/bin/systemctl','daemon-reload'],check=True)
 real_run(['/usr/bin/systemctl','start','ai-device-native-helper.service'],check=True)
 deadline=time.monotonic()+10
 while not socket_path.exists():
  assert time.monotonic()<deadline,'helper socket not ready'
  time.sleep(.05)
 replies=[]; errors=[]
 def start():
  try: replies.append(helper.call_helper(socket_path,{'action':'start','instance_id':'probe','operation_id':mode}))
  except Exception as exc: errors.append(str(exc))
 thread=threading.Thread(target=start); thread.start()
 paused_guard=None
 try:
  deadline=time.monotonic()+10
  while True:
   sub=real_run(['/usr/bin/systemctl','show','ai-device-client-probe.service','--property=SubState','--value'],check=True,capture_output=True,text=True).stdout.strip()
   if sub=='start-pre': break
   assert time.monotonic()<deadline,'client did not enter start-pre'
   time.sleep(.05)
  assert (registry/f'op-probe-{mode}.json').exists()
  if mode in ('start-daemon-crash','start-daemon-stop','start-guard-pause'):
   import signal
   pid=int(real_run(['/usr/bin/systemctl','show','ai-device-native-helper.service','--property=MainPID','--value'],check=True,capture_output=True,text=True).stdout)
   argv=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
   assert b'device_runtime.native_helper' in argv and b'serve' in argv,argv
   if mode=='start-guard-pause':
    processes=real_run(['/usr/bin/ps','-eo','pid=,ppid='],check=True,capture_output=True,text=True).stdout.splitlines()
    children=[int(parts[0]) for line in processes if len(parts:=line.split())==2 and int(parts[1])==pid]
    assert len(children)==1,children
    guard=children[0]
    guard_argv=Path(f'/proc/{guard}/cmdline').read_bytes().split(b'\0')
    assert b'-c' in guard_argv and any(b'sender_reaped' in arg for arg in guard_argv),guard_argv
    os.kill(guard,signal.SIGSTOP); paused_guard=guard
    deadline=time.monotonic()+5
    while True:
     state=Path(f'/proc/{guard}/status').read_text()
     if any(line.startswith('State:') and line.split()[1]=='T' for line in state.splitlines()): break
     assert time.monotonic()<deadline,'guard did not stop'
     time.sleep(.05)
   if mode in ('start-daemon-crash','start-guard-pause'):
    os.kill(pid,signal.SIGKILL) # Only this disposable test unit's checked MainPID.
   else:
    real_run(['/usr/bin/systemctl','stop','ai-device-native-helper.service'],check=True)
   thread.join(timeout=5)
   assert not thread.is_alive() and errors and not replies,(errors,replies)
   try:
    with helper.package_lease(registry,exclusive=True): pass
   except helper.HelperError as error: assert 'still executing' in str(error),error
   else: raise AssertionError('orphan guard did not retain package lease')
   # An explicit early reconnect must not create another start while the old
   # sender owns the lock and its intent is still unconfirmed.
   assert helper.recover_socket(registry,socket_path)['removed'] is True
   if mode in ('start-daemon-crash','start-guard-pause'):
    real_run(['/usr/bin/systemctl','reset-failed','ai-device-native-helper.service'],check=True)
   real_run(['/usr/bin/systemctl','start','ai-device-native-helper.service'],check=True)
   deadline=time.monotonic()+5
   while not socket_path.exists():
    assert time.monotonic()<deadline
    time.sleep(.05)
   early=helper.call_helper(socket_path,{'action':'start','instance_id':'probe','operation_id':'during-old-sender'})
   assert early.get('ok') is False,early
   assert not (registry/'op-probe-during-old-sender.json').exists()
   receipt=registry/f'sender-probe-{mode}.json'
   if mode=='start-guard-pause':
    deadline=time.monotonic()+20
    while True:
     observation=helper.Systemd().query('ai-device-client-probe.service')
     if observation['Job'] in ('','0'): break
     assert time.monotonic()<deadline,observation
     time.sleep(.1)
    assert not receipt.exists()
    try:
     with helper.package_lease(registry,exclusive=True): pass
    except helper.HelperError as error: assert 'still executing' in str(error),error
    else: raise AssertionError('paused guard released package lock')
    refused=helper.call_helper(socket_path,{'action':'resolve-running','instance_id':'probe','operation_id':mode})
    assert refused.get('ok') is False,refused
    assert not (registry/f'recovery-probe-{mode}.json').exists()
    print(json.dumps({'state':'paused_guard_blocks_recovery','systemd_job_terminal':True,'receipt_missing':True,'package_lease_held':True,'recovery_refused':True}))
    os.kill(paused_guard,signal.SIGCONT); paused_guard=None
   deadline=time.monotonic()+20
   while not receipt.exists():
    assert time.monotonic()<deadline,'orphan guard receipt not published'
    time.sleep(.05)
   with helper.package_lease(registry,exclusive=True): pass
   assert not (registry/f'result-probe-{mode}.json').exists()
   same=helper.call_helper(socket_path,{'action':'start','instance_id':'probe','operation_id':mode})
   assert same['ok'] and same['result']['result']['state']=='unknown' and same['result']['replayed'] is False,same
   resolved=helper.call_helper(socket_path,{'action':'resolve-running','instance_id':'probe','operation_id':mode})
   assert resolved['ok'] and resolved['result']['recovery']['state']=='confirmed_running' and resolved['result']['recovery']['start_replayed'] is False,resolved
   assert not (registry/f'result-probe-{mode}.json').exists()
   print(json.dumps({'state':'daemon_exit_guard_reaped_and_reconciled','exit_mode':mode,'lost_response':True,'package_lease_held':True,'early_restart_new_start_refused':True,'sender_receipt_present':True,'same_id_not_replayed':True,'explicit_running_recovery':True}))
   raise SystemExit(0)
  if mode=='start-guard-crash':
   import signal
   pid=int(real_run(['/usr/bin/systemctl','show','ai-device-native-helper.service','--property=MainPID','--value'],check=True,capture_output=True,text=True).stdout)
   processes=real_run(['/usr/bin/ps','-eo','pid=,ppid='],check=True,capture_output=True,text=True).stdout.splitlines()
   children=[int(parts[0]) for line in processes if len(parts:=line.split())==2 and int(parts[1])==pid]
   assert len(children)==1,children
   guard=children[0]
   argv=Path(f'/proc/{guard}/cmdline').read_bytes().split(b'\0')
   assert b'-c' in argv and any(b'sender_reaped' in arg for arg in argv),argv
   os.kill(guard,signal.SIGKILL) # Only the identified child of this disposable daemon.
   thread.join(timeout=15)
   assert not thread.is_alive() and not errors and replies and replies[0]['result']['result']['state']=='unknown',(errors,replies)
   assert not (registry/f'sender-probe-{mode}.json').exists()
   deadline=time.monotonic()+20
   while True:
    observation=helper.Systemd().query('ai-device-client-probe.service')
    if observation['Job'] in ('','0'): break
    assert time.monotonic()<deadline,observation
    time.sleep(.1)
   for action,operation in [('resolve-running',mode),('start','after-crash')]:
    response=helper.call_helper(socket_path,{'action':action,'instance_id':'probe','operation_id':operation})
    assert response.get('ok') is False,response
   assert not (registry/'op-probe-after-crash.json').exists()
   assert not (registry/f'recovery-probe-{mode}.json').exists()
   # Stop only the isolated test daemon, preserving its real ledger and socket.
   result_path=registry/f'result-probe-{mode}.json'
   before=result_path.read_bytes()
   real_run(['/usr/bin/systemctl','stop','ai-device-native-helper.service'],check=True)
   assert socket_path.exists(),'expected preserved stale socket'
   assert helper.recover_socket(registry,socket_path)['removed'] is True
   real_run(['/usr/bin/systemctl','start','ai-device-native-helper.service'],check=True)
   deadline=time.monotonic()+10
   while not socket_path.exists():
    assert time.monotonic()<deadline,'restarted helper socket not ready'
    time.sleep(.05)
   same=helper.call_helper(socket_path,{'action':'start','instance_id':'probe','operation_id':mode})
   assert same['ok'] and same['result']['result']['state']=='unknown' and same['result']['replayed'] is False,same
   for action,operation in [('resolve-running',mode),('start','after-restart')]:
    response=helper.call_helper(socket_path,{'action':action,'instance_id':'probe','operation_id':operation})
    assert response.get('ok') is False,response
   assert result_path.read_bytes()==before and not (registry/'op-probe-after-restart.json').exists()
   assert not (registry/f'recovery-probe-{mode}.json').exists()
   print(json.dumps({'state':'restarted_daemon_preserves_unknown','socket_recovered_explicitly':True,'same_id_not_replayed':True,'new_start_refused':True}))
   print(json.dumps({'state':'real_guard_crash_remains_unknown','missing_receipt':True,'recovery_refused':True,'new_start_refused':True,'systemd_job_terminal':True}))
   raise SystemExit(0)
  try:
   with helper.package_lease(registry,exclusive=True): pass
  except helper.HelperError as error: assert 'still executing' in str(error),error
  else: raise AssertionError('package lease not held by start')
  job._require_proven_job_fence=lambda: None
  try: job.dispatch_job(manager,'probe','job-one',runner=runner)
  except (helper.HelperError,update.UpdateError) as error: print(json.dumps({'state':'same_instance_job_refused','error':str(error)}))
  else: raise AssertionError('job overlapped helper start')
  assert not stem.with_suffix('.job-intent.json').exists() and not calls
 finally:
  if paused_guard is not None:
   os.kill(paused_guard,signal.SIGCONT)
  thread.join(timeout=30)
 assert not thread.is_alive() and not errors and replies and replies[0].get('ok') is True,(errors,replies)
 with helper.package_lease(registry,exclusive=True): pass
 print(json.dumps({'state':'real_start_holds_package_lease','opposing_lease_refused':True,'released_after_start':True,'job_intent_created':False}))
 raise SystemExit(0)
# All remaining dispatch behavior is a deliberately isolated test substitute.
# It is not evidence that production dispatch is authorized or safe to enable.
job._require_proven_job_fence=lambda: None
print(json.dumps({'state':'disposable_test_fence_substitute','production_dispatch_allowed':False}))
first=job.dispatch_job(manager,'probe','job-one',runner=runner)
assert first['state']==('dispatch_unknown' if mode=='lost-response' else 'dispatch_accepted'),first
second=job.dispatch_job(manager,'probe','job-one')
assert second['state']=='dispatch_already_recorded' and not second['replayed']
assert len(calls)==1
if mode in ('source-drift','close-before-apply','retire-retry'):
 deadline=time.monotonic()+10
 while True:
  queued=real_run(['/usr/bin/systemctl','show',first['unit'],'--property=SubState','--value'],check=True,capture_output=True,text=True).stdout.strip()
  if queued=='start-pre': break
  assert time.monotonic()<deadline,'test did not observe queued start-pre window'
  time.sleep(.05)
 if mode=='source-drift':
  # Start the real helper only in this disposable systemd environment, then
  # issue a real IPC start while the test job's persisted intent is queued.
  runtime=Path('/run/device-helper-acceptance')
  runtime.mkdir(mode=0o755)
  socket_path=runtime/'helper.sock'
  unit=Path('/etc/systemd/system/ai-device-native-helper.service')
  unit.write_text(helper.render_helper_unit(bootstrap_python=Path(sys.executable),registry=registry,socket_path=socket_path,group=0))
  real_run(['/usr/bin/systemctl','daemon-reload'],check=True)
  real_run(['/usr/bin/systemctl','start','ai-device-native-helper.service'],check=True)
  deadline=time.monotonic()+10
  while not socket_path.exists():
   assert time.monotonic()<deadline,'helper socket not ready'
   time.sleep(.05)
  reply=helper.call_helper(socket_path,{'action':'start','instance_id':'probe','operation_id':'fence-check'})
  assert reply=={'ok':False,'error':'helper_request_refused'},reply
  assert not (registry/'op-probe-fence-check.json').exists()
  observed=real_run(['/usr/bin/systemctl','show','ai-device-client-probe.service','--property=ActiveState,MainPID','--no-pager'],check=True,capture_output=True,text=True).stdout
  assert 'ActiveState=inactive' in observed and 'MainPID=0' in observed,observed
  print(json.dumps({'state':'real_helper_start_fenced_by_queued_job','start_intent_created':False,'client_started':False}))
  value=json.loads(active); value['release_version']='0.0.0-test-drift'
  (root/'active.json').write_text(json.dumps(value))
 else:
  closed=job.close_before_apply(manager,'probe','job-one')
  assert closed['state']=='closed_before_apply' and closed['fence_retained'] and not closed['retry_allowed']
  assert job.close_before_apply(manager,'probe','job-one')==closed
  if mode=='retire-retry':
   retired=job.retire_closed_job(manager,'probe','job-one')
   assert retired['state']=='retired_before_apply' and not retired['old_job_replay_allowed']
   assert job.retire_closed_job(manager,'probe','job-one')==retired
if mode=='retire-retry':
 helper._assert_no_package_intent(registry)
else:
 try: helper._assert_no_package_intent(registry)
 except helper.HelperError: pass
 else: raise AssertionError('queued job must fence starts')
deadline=time.monotonic()+90
if mode in ('source-drift','close-before-apply','retire-retry'):
 while True:
  status=real_run(['/usr/bin/systemctl','show',first['unit'],'--property=ActiveState','--value'],check=True,capture_output=True,text=True).stdout.strip()
  if status=='failed': break
  assert time.monotonic()<deadline,'worker did not reject source drift'
  time.sleep(.1)
 assert not stem.with_suffix('.apply-intent.json').exists()
 assert not stem.with_suffix('.sender.json').exists()
 assert not stem.with_suffix('.package.log').exists()
 assert stem.with_suffix('.job-intent.json').exists()
 error=Path('/var/lib/ai-device/worker-test-error.log').read_text()
 assert ('source active changed' if mode=='source-drift' else 'package job closed before execution') in error,error
 diagnostic('job_failed_requires_review')
 if mode=='source-drift':
  (root/'active.json').write_bytes(active)
  print(json.dumps({'state':'queued_source_drift_refused','dispatch_once':True,'package_executed':False,'persistent_fence_retained':True,'test_active_restored':True}))
 elif mode=='retire-retry':
  assert stem.with_suffix('.job-closed.json').exists() and stem.with_suffix('.job-retired.json').exists()
  try: manager.apply('probe','job-one')
  except update.UpdateError: pass
  else: raise AssertionError('old ID must never replay')
  manager.prepare('probe','job-two',package,sha256=helper._digest(package),version='0.2.2-1')
  new=registry/'updates/5-probe--job-two'
  manager.approve('probe','job-two',prepared_sha256=helper._digest(new.with_suffix('.prepared.json')))
  again=job.dispatch_job(manager,'probe','job-two')
  assert again['state']=='dispatch_accepted'
  deadline=time.monotonic()+90
  while not new.with_suffix('.outcome.json').exists():
   assert time.monotonic()<deadline,'new job outcome missing'
   time.sleep(.1)
  new_sender=json.loads(new.with_suffix('.sender.json').read_bytes())
  assert new_sender['returncode']!=0 and 'helper transaction required' in new.with_suffix('.package.log').read_text()
  assert not stem.with_suffix('.apply-intent.json').exists() and (root/'active.json').read_bytes()==active
  try: helper._assert_no_package_intent(registry)
  except helper.HelperError: pass
  else: raise AssertionError('new job must retain fence')
  print(json.dumps({'state':'retired_old_delayed_worker_refused_new_id_dispatched','old_job_replayed':False,'new_candidate_guard_refused':True,'new_fence_retained':True}))
 else:
  assert stem.with_suffix('.job-closed.json').exists()
  assert (root/'active.json').read_bytes()==active
  try: helper._assert_no_package_intent(registry)
  except helper.HelperError: pass
  else: raise AssertionError('closed job must retain fence')
  print(json.dumps({'state':'delayed_worker_refused_after_close','dispatch_once':True,'package_executed':False,'persistent_fence_retained':True}))
 raise SystemExit(0)
while not stem.with_suffix('.outcome.json').exists():
 if time.monotonic()>deadline:
  subprocess.run(['/usr/bin/journalctl','-u',first['unit'],'--no-pager','-n','40'],check=False)
  raise AssertionError('job outcome missing')
 time.sleep(.1)
intent=json.loads(stem.with_suffix('.apply-intent.json').read_bytes())
sender=json.loads(stem.with_suffix('.sender.json').read_bytes())
outcome=json.loads(stem.with_suffix('.outcome.json').read_bytes())
assert sender['intent']==intent and sender['returncode']!=0,sender
assert outcome['state']=='unknown' and outcome['intent']==intent,outcome
assert 'helper transaction required' in stem.with_suffix('.package.log').read_text()
assert (root/'active.json').read_bytes()==active
report=diagnostic('package_outcome_recorded')
assert report['outcome']==outcome
# Only the disposable fixture's receipt is temporarily hidden and restored.
# Test this before any recovery receipt is written.
sender_path=stem.with_suffix('.sender.json')
hidden=stem.with_suffix('.sender.fixture-hidden')
sender_path.rename(hidden)
try:
 try:
  job.recover_job(manager,'probe','job-one')
 except (OSError, ValueError):
  pass
 else:
  raise AssertionError('missing sender must refuse recovery')
 assert not stem.with_suffix('.recovery.json').exists()
 assert not stem.with_suffix('.job-recovery.json').exists()
finally:
 hidden.rename(sender_path)
# The transient unit may still be exiting just after the outcome is durable.
while True:
 report=job.observe_job(manager,'probe','job-one')
 unit=report['unit_observation'] or {}
 if unit.get('LoadState')=='loaded' and (unit.get('ActiveState') in ('inactive','failed') or (unit.get('ActiveState')=='active' and unit.get('SubState')=='exited' and unit.get('Result')=='success')) and unit.get('MainPID')=='0' and unit.get('Job') in ('','0'): break
 assert time.monotonic()<deadline,'unit did not reach observable terminal state'
 time.sleep(.1)
# General recovery must not bypass the job-specific entry when an intent exists.
before={p.name:p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}
try: manager.recover('probe','job-one')
except update.UpdateError as error: assert 'job recovery requires explicit job binding' in str(error)
else: raise AssertionError('generic recovery accepted job intent')
assert {p.name:p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}==before
assert len(calls)==1 and (root/'active.json').read_bytes()==active
print('GENERIC_RECOVERY_JOB_INTENT_REFUSED')
orphan=stem.with_suffix('.job-recovery.json')
orphan.write_text(json.dumps({'retry_allowed':0,'fence_retained':1}))
before={p.name:p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}
try:
 try: job.recover_job(manager,'probe','job-one')
 except update.UpdateError as error: assert 'job recovery evidence without underlying recovery' in str(error)
 else: raise AssertionError('orphan job recovery accepted')
 assert {p.name:p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}==before
 assert len(calls)==1 and (root/'active.json').read_bytes()==active
finally:
 orphan.unlink()  # This file belongs exclusively to the disposable fixture.
print('ORPHAN_JOB_RECOVERY_REFUSED_BEFORE_WRITE')
first_recovery=job.recover_job(manager,'probe','job-one')
second_recovery=job.recover_job(manager,'probe','job-one')
assert first_recovery==second_recovery
assert first_recovery['state']=='job_reconciled_without_replay'
assert first_recovery['retry_allowed'] is False and first_recovery['fence_retained'] is True
receipt_path=stem.with_suffix('.job-recovery.json')
original_receipt=receipt_path.read_bytes()
try:
 for key, bad in (('retry_allowed',0),('fence_retained',1)):
  receipt_path.write_text(json.dumps({**first_recovery,key:bad}))
  before={p.name:p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}
  try: job.recover_job(manager,'probe','job-one')
  except update.UpdateError as error: assert 'job recovery evidence changed' in str(error)
  else: raise AssertionError('type-drifted job recovery accepted')
  assert {p.name:p.read_bytes() for p in stem.parent.iterdir() if p.is_file()}==before
  assert len(calls)==1 and (root/'active.json').read_bytes()==active
finally:
 receipt_path.write_bytes(original_receipt)
assert job.recover_job(manager,'probe','job-one')==first_recovery
print('JOB_RECOVERY_TYPE_DRIFT_REFUSED')
assert len(calls)==1 and (root/'active.json').read_bytes()==active
assert stem.with_suffix('.job-intent.json').exists() and stem.with_suffix('.apply-intent.json').exists()
try: helper._assert_no_package_intent(registry)
except helper.HelperError: pass
else: raise AssertionError('recovery must retain the fence')
print(json.dumps({'state':'explicit_job_reconciliation_verified','missing_sender_refused':True,'idempotent':True,'dispatch_once':True,'fence_retained':True,'active_unchanged':True}))
show=subprocess.run(['/usr/bin/systemctl','show',first['unit'],'--property=Restart,User,ExecStart,ActiveState,Result'],check=True,capture_output=True,text=True)
print(show.stdout)
assert 'Restart=no' in show.stdout
print(json.dumps({'state':'independent_job_refused_by_package_guard','dispatch_once':True,'sender_receipt_matched':True,'outcome':'unknown','active_unchanged':True,'device_service_started':False}))
PY
