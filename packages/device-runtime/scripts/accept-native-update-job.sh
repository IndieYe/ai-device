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
registry=Path('/var/lib/ai-device/manager')
root=Path('/home/probe/instance')
package=Path('/var/lib/ai-device/inputs/candidate.deb')
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
assert mode in ('none','lost-response','source-drift','close-before-apply','retire-retry')
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
first_recovery=job.recover_job(manager,'probe','job-one')
second_recovery=job.recover_job(manager,'probe','job-one')
assert first_recovery==second_recovery
assert first_recovery['state']=='job_reconciled_without_replay'
assert first_recovery['retry_allowed'] is False and first_recovery['fence_retained'] is True
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
