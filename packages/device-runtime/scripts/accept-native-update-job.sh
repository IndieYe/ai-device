#!/bin/sh
# Disposable systemd Linux container only. Real Device preinst must reject apply.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = job-v1 ] || exit 2
[ "$(cat /proc/1/comm)" = systemd ] && [ "$(id -u)" = 0 ] || exit 2
[ "$#" = 2 ] || { echo 'usage: SCRIPT FULL_MANAGER_DEB CURRENT_WHEELS' >&2; exit 2; }
[ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
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
import json,time,subprocess
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
first=job.dispatch_job(manager,'probe','job-one')
assert first['state']=='dispatch_accepted',first
second=job.dispatch_job(manager,'probe','job-one')
assert second['state']=='dispatch_already_recorded' and not second['replayed']
try: helper._assert_no_package_intent(registry)
except helper.HelperError: pass
else: raise AssertionError('queued job must fence starts')
deadline=time.monotonic()+90
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
show=subprocess.run(['/usr/bin/systemctl','show',first['unit'],'--property=Restart,User,ExecStart,ActiveState,Result'],check=True,capture_output=True,text=True)
print(show.stdout)
assert 'Restart=no' in show.stdout
print(json.dumps({'state':'independent_job_refused_by_package_guard','dispatch_once':True,'sender_receipt_matched':True,'outcome':'unknown','active_unchanged':True,'device_service_started':False}))
PY
