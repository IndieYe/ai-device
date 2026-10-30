#!/bin/sh
# Disposable systemd only: snapshot worker default-deny; no real package update.
# Uses an inert source package and extracted payload; not a clean-install test.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = job-v1 ] || exit 2
[ "$(cat /proc/1/comm)" = systemd ] && [ "$(id -u)" = 0 ] || exit 2
[ "$#" = 2 ] || { echo 'usage: SCRIPT FULL_MANAGER_DEB CURRENT_WHEELS' >&2; exit 2; }
printf '%s  %s\n' 93043dfd7ed26ac38ba22f52f8320d5041e882daeada149fd81ef2240376fddd "$1" | sha256sum -c -
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
"$P/runtime/bin/python3.13" -I -B - "$2/ai_device-0.2.2-py3-none-any.whl" <<'PY'
import sys, zipfile
from pathlib import Path
from device_runtime import native_helper, native_update, native_update_job
with zipfile.ZipFile(sys.argv[1]) as wheel:
    for module in (native_helper, native_update, native_update_job):
        path=Path(module.__file__)
        assert path.read_bytes()==wheel.read('device_runtime/'+path.name), path
print('INSTALLED_WORKER_MODULES_MATCH_WHEEL', flush=True)
PY
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
with manager._transaction('probe','job-one') as canonical:
    assert manager._transaction_locked('probe','job-one') == canonical
    try:
        with manager.helper._lease('probe'):
            raise AssertionError('unexpectedly acquired active instance lease')
    except helper.HelperError as error:
        assert 'instance management busy' in str(error), error
print('LOCKED_TRANSACTION_MATCHES_CANONICAL_WITH_INSTANCE_LEASE', flush=True)
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
assert mode in ('none','lost-response','source-drift','close-before-apply','retire-retry','start-lock','start-guard-crash','start-daemon-crash','start-daemon-stop','start-guard-pause','observation-race')
calls=[]
real_run=subprocess.run
def runner(argv,**kwargs):
 calls.append(argv)
 if mode in ('source-drift','close-before-apply','retire-retry','observation-race'):
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
import hashlib
snapshot=stem.with_suffix('.bootstrap')
job.bootstrap.snapshot_runtime(Path(sys.base_prefix),snapshot)
unit='ai-device-worker-gate-fixture.service'
intent={'schema_version':1,'state':'job_dispatch_intent','close_protocol':'package-lease-v1',
 'unit':'ai-device-package-'+hashlib.sha256(str(stem).encode()).hexdigest()+'.service',
 'instance_id':'probe','operation_id':'job-one',
 'prepared_sha256':helper._digest(stem.with_suffix('.prepared.json')),
 'runtime_snapshot_sha256':helper._digest(snapshot/'snapshot.json')}
path=stem.with_suffix('.job-intent.json')
helper._save_new(path,intent)
original=path.read_bytes()
observed=job.dispatch_job(manager,'probe','job-one',runner=runner)
assert observed['state']=='dispatch_already_recorded' and observed['replayed'] is False
assert not calls and path.read_bytes()==original
print('EXISTING_INTENT_OBSERVED_WITHOUT_REDISPATCH',flush=True)
argv=['/usr/bin/systemd-run','--wait','--unit='+unit,'--service-type=oneshot','--property=Restart=no','--property=StandardError=file:/var/lib/ai-device/worker-gate-fixture.err','--',
 str(snapshot/'runtime/bin/python3.13'),'-I','-B','-m','device_runtime.native_update_job',
 '--registry',str(registry),'--instance-id','probe','--operation-id','job-one',
 '--job-intent-sha256',helper._digest(path)]
res=real_run(argv,check=False,capture_output=True,text=True,timeout=90)
status=real_run(['/usr/bin/systemctl','show',unit,'--property=Result,ExecMainStatus','--no-pager'],check=True,capture_output=True,text=True,timeout=10).stdout
assert res.returncode != 0 and 'Result=exit-code' in status and 'ExecMainStatus=1' in status,(res.returncode,status)
stderr=Path('/var/lib/ai-device/worker-gate-fixture.err').read_text()
assert 'job worker disabled: execution fence unproven' in stderr, stderr
assert path.read_bytes()==original and not stem.with_suffix('.apply-intent.json').exists()
assert not stem.with_suffix('.sender.json').exists() and not stem.with_suffix('.outcome.json').exists()
assert (root/'active.json').read_bytes()==active
print('SNAPSHOT_WORKER_SYSTEMD_NEW_ACTION_DEFAULT_DENIED_NO_WRITES',flush=True)
PY
