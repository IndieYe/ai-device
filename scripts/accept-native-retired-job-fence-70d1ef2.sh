#!/bin/sh
# One fresh disposable systemd container only; no actual Device start or package upgrade.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = retired-job-fence-v1 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(cat /proc/1/comm)" = systemd ] || exit 2
[ "$#" = 1 ] || exit 2
[ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 40b0755e0a47f343a100e21edc29d9d0ab6ad59283e41aa9fc4e54cb61d6bed6 "$1" | sha256sum -c -
dpkg -i "$1"
P=/opt/ai-device/packages/0.2.2-native1
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
useradd -m -u 12345 probe
runuser -u probe -- "$P/runtime/bin/python3.13" -I -B -m device_runtime.native_install --payload "$P" --root /home/probe/instance --instance-id probe --server-url http://127.0.0.1:9
mkdir -p /var/lib/ai-device/manager /run/device-retired-fence-probe
printf 'TEST_ONLY=yes\n' > /etc/device-retired-fence.env
chmod 600 /etc/device-retired-fence.env
"$B" -I -B - <<'PY'
import hashlib, subprocess, time
from pathlib import Path
from device_runtime import native_helper as h
registry=Path('/var/lib/ai-device/manager')
endpoint=Path('/run/device-retired-fence-probe/helper.sock')
h.register(registry,Path('/etc/systemd/system'),instance_id='probe',root=Path('/home/probe/instance'),username='probe',environment_file='/etc/device-retired-fence.env')
unit=Path('/etc/systemd/system/ai-device-native-helper.service')
unit.write_text(h.render_helper_unit(bootstrap_python=Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13'),registry=registry,socket_path=endpoint,group=0))
subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True)
subprocess.run(['/usr/bin/systemctl','start','ai-device-native-helper.service'],check=True)
deadline=time.monotonic()+15
while not endpoint.exists():
 if time.monotonic()>deadline: raise TimeoutError('helper socket')
 time.sleep(.05)
query=h.call_helper(endpoint,{'action':'query','instance_id':'probe','operation_id':'before'})
assert query['ok'] is True and query['result']['instance_id']=='probe' and query['result']['intent'] is None
updates=registry/'updates'; updates.mkdir(mode=0o700)
stem=updates/'5-probe--old'
intent=stem.with_suffix('.job-intent.json')
closed=stem.with_suffix('.job-closed.json')
retired=stem.with_suffix('.job-retired.json')
h._save_new(intent,{'schema_version':1,'state':'job_dispatch_intent','close_protocol':'package-lease-v1','instance_id':'probe','operation_id':'old','unit':'ai-device-package-'+hashlib.sha256(str(stem).encode()).hexdigest()+'.service','prepared_sha256':'a'*64,'runtime_snapshot_sha256':'b'*64})
h._save_new(closed,{'state':'closed_before_apply','job_intent_sha256':h._digest(intent),'retry_allowed':False,'fence_retained':True})
h._save_new(retired,{'state':'retired_before_apply','job_intent_sha256':h._digest(intent),'closed_sha256':h._digest(closed),'old_job_replay_allowed':False})
assert h._job_retired(intent) and intent in h._pending_package_jobs(updates)
before=[p.read_bytes() for p in (intent,closed,retired)]
try:
 h.Helper(registry).handle({'action':'start','instance_id':'probe','operation_id':'direct-check'},peer_uid=0)
except h.HelperError as error:
 assert 'package transaction blocks start' in str(error),error
else:
 raise AssertionError('direct start unexpectedly allowed')
reply=h.call_helper(endpoint,{'action':'start','instance_id':'probe','operation_id':'blocked'})
assert reply=={'ok':False,'error':'helper_request_refused'},reply
assert [p.read_bytes() for p in (intent,closed,retired)]==before
assert not (registry/'op-probe-blocked.json').exists()
assert not (registry/'op-probe-direct-check.json').exists()
state=subprocess.run(['/usr/bin/systemctl','show','ai-device-client-probe.service','--property=ActiveState,MainPID','--no-pager'],check=True,capture_output=True,text=True).stdout
assert 'ActiveState=inactive' in state and 'MainPID=0' in state,state
print('REAL_HELPER_REFUSES_START_WITH_STRUCTURALLY_RETIRED_JOB',flush=True)
print('RETIRED_EVIDENCE_PRESERVED_NO_START_INTENT_OR_SERVICE',flush=True)
PY
