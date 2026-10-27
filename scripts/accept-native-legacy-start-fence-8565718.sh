#!/bin/sh
# Fresh disposable systemd container only. No real device or service start.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = legacy-start-fence-v1 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(cat /proc/1/comm)" = systemd ] || exit 2
[ "$#" = 1 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' f37cf12b960824e46ae7de38ac71b8027d384016aa1469d07b165ce8fd7b0eec "$1" | sha256sum -c -
dpkg -i "$1"
P=/opt/ai-device/packages/0.2.2-native1
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
useradd -m -u 12345 probe
runuser -u probe -- "$P/runtime/bin/python3.13" -I -B -m device_runtime.native_install --payload "$P" --root /home/probe/instance --instance-id probe --server-url http://127.0.0.1:9
mkdir -p /var/lib/ai-device/manager /run/device-legacy-start-fence
printf 'TEST_ONLY=yes\n' > /etc/device-legacy-start-fence.env
chmod 600 /etc/device-legacy-start-fence.env
"$B" -I -B - <<'PY'
import subprocess, time
from pathlib import Path
from device_runtime import native_helper as h
registry=Path('/var/lib/ai-device/manager'); endpoint=Path('/run/device-legacy-start-fence/helper.sock')
record=h.register(registry,Path('/etc/systemd/system'),instance_id='probe',root=Path('/home/probe/instance'),username='probe',environment_file='/etc/device-legacy-start-fence.env')
# register may return provisioning metadata; read the authoritative binding.
record=h.Helper(registry)._binding('probe')
unit=Path('/etc/systemd/system/ai-device-native-helper.service')
unit.write_text(h.render_helper_unit(bootstrap_python=Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13'),registry=registry,socket_path=endpoint,group=0))
subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True)
subprocess.run(['/usr/bin/systemctl','start','ai-device-native-helper.service'],check=True)
deadline=time.monotonic()+15
while not endpoint.exists():
 if time.monotonic()>deadline: raise TimeoutError('helper socket')
 time.sleep(.05)
intent={'action':'start','instance_id':'probe','operation_id':'old','unit_sha256':record['unit_sha256'],'execution_barrier':1}
intent_path=registry/'op-probe-old.json'; result_path=registry/'result-probe-old.json'; recovery=registry/'recovery-probe-old.json'
h._save_new(intent_path,intent); h._save_new(result_path,{'state':'unknown'})
for action in ('resolve-running','resolve-stopped'):
 try:
  h.Helper(registry).handle({'action':action,'instance_id':'probe','operation_id':'old'},peer_uid=0)
 except h.HelperError as error:
  assert 'legacy start sender completion unproven' in str(error),error
 else: raise AssertionError('legacy recovery unexpectedly accepted')
assert not recovery.exists()
h._save_new(recovery,{'state':'confirmed_running','intent':intent,'start_replayed':False})
before=[p.read_bytes() for p in (intent_path,result_path,recovery)]
query=h.call_helper(endpoint,{'action':'query','instance_id':'probe','operation_id':'old'})
assert query['ok'] is True and query['result']['recovery']['state']=='confirmed_running'
try:
 h.Helper(registry).handle({'action':'start','instance_id':'probe','operation_id':'direct'},peer_uid=0)
except h.HelperError as error:
 assert 'legacy start sender completion unproven' in str(error),error
else: raise AssertionError('legacy recovery authorized new start')
reply=h.call_helper(endpoint,{'action':'start','instance_id':'probe','operation_id':'next'})
assert reply=={'ok':False,'error':'helper_request_refused'},reply
assert [p.read_bytes() for p in (intent_path,result_path,recovery)]==before
assert not (registry/'op-probe-next.json').exists() and not (registry/'op-probe-direct.json').exists()
state=subprocess.run(['/usr/bin/systemctl','show','ai-device-client-probe.service','--property=ActiveState,MainPID','--no-pager'],check=True,capture_output=True,text=True).stdout
assert 'ActiveState=inactive' in state and 'MainPID=0' in state,state
print('LEGACY_RECOVERY_READABLE_BUT_CANNOT_AUTHORIZE_NEW_START',flush=True)
print('LEGACY_RESOLVE_REFUSED_NO_NEW_INTENT_OR_DEVICE_START',flush=True)
PY
