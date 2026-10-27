#!/bin/sh
# Fresh isolated systemd only; no actual Device start or package operation.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = sender-tree-fence-v1 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(cat /proc/1/comm)" = systemd ] || exit 2
[ "$#" = 1 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 156ca247130f6a0808b9cd82cb4a4d062b4d255de08c7f5c5ed312e2c72a3792 "$1" | sha256sum -c -
dpkg -i "$1"
P=/opt/ai-device/packages/0.2.2-native1
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
useradd -m -u 12345 probe
runuser -u probe -- "$P/runtime/bin/python3.13" -I -B -m device_runtime.native_install --payload "$P" --root /home/probe/instance --instance-id probe --server-url http://127.0.0.1:9
mkdir -p /var/lib/ai-device/manager /run/device-sender-tree-fence
printf 'TEST_ONLY=yes\n' > /etc/device-sender-tree-fence.env
chmod 600 /etc/device-sender-tree-fence.env
"$B" -I -B - <<'PY'
import subprocess, time
from pathlib import Path
from device_runtime import native_helper as h
registry=Path('/var/lib/ai-device/manager'); endpoint=Path('/run/device-sender-tree-fence/helper.sock')
h.register(registry,Path('/etc/systemd/system'),instance_id='probe',root=Path('/home/probe/instance'),username='probe',environment_file='/etc/device-sender-tree-fence.env')
record=h.Helper(registry)._binding('probe')
unit=Path('/etc/systemd/system/ai-device-native-helper.service')
unit.write_text(h.render_helper_unit(bootstrap_python=Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13'),registry=registry,socket_path=endpoint,group=0))
subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True)
subprocess.run(['/usr/bin/systemctl','start','ai-device-native-helper.service'],check=True)
deadline=time.monotonic()+15
while not endpoint.exists():
 if time.monotonic()>deadline: raise TimeoutError('helper socket')
 time.sleep(.05)
def request(action,operation): return {'action':action,'instance_id':'probe','operation_id':operation}
intent={'action':'start','instance_id':'probe','operation_id':'unknown','unit_sha256':record['unit_sha256'],'execution_barrier':2}
intent_path=registry/'op-probe-unknown.json'; result=registry/'result-probe-unknown.json'; sender=registry/'sender-probe-unknown.json'; recovery=registry/'recovery-probe-unknown.json'
h._save_new(intent_path,intent); h._save_new(result,{'state':'unknown'}); h._save_new(sender,{'state':'sender_reaped','intent':intent,'returncode':0})
for action in ('resolve-running','resolve-stopped'):
 try: h.Helper(registry).handle(request(action,'unknown'),peer_uid=0)
 except h.HelperError as error: assert 'sender process tree completion unproven' in str(error),error
 else: raise AssertionError('direct receipt allowed recovery')
 assert h.call_helper(endpoint,request(action,'unknown'))=={'ok':False,'error':'helper_request_refused'}
assert not recovery.exists()
h._save_new(recovery,{'state':'confirmed_stopped','intent':intent,'start_replayed':False})
query=h.call_helper(endpoint,request('query','unknown'))
assert query['ok'] is True and query['result']['recovery']['state']=='confirmed_stopped'
try: h.Helper(registry).handle(request('start','blocked'),peer_uid=0)
except h.HelperError as error: assert 'sender process tree completion unproven' in str(error),error
else: raise AssertionError('existing recovery allowed start')
assert h.call_helper(endpoint,request('start','blocked'))=={'ok':False,'error':'helper_request_refused'}
# Isolate the separate started-result case using a distinct instance ID via a
# fresh registry view is unnecessary: replace only the test result, preserving
# the original bytes for comparison and restore them before ending the fixture.
original=result.read_bytes(); result.write_text('{"state":"started"}')
try:
 try: h.Helper(registry).handle(request('start','started-blocked'),peer_uid=0)
 except h.HelperError as error: assert 'sender process tree completion unproven' in str(error),error
 else: raise AssertionError('started sender allowed new start')
finally: result.write_bytes(original)
assert not (registry/'op-probe-blocked.json').exists() and not (registry/'op-probe-started-blocked.json').exists()
state=subprocess.run(['/usr/bin/systemctl','show','ai-device-client-probe.service','--property=ActiveState,MainPID','--no-pager'],check=True,capture_output=True,text=True).stdout
assert 'ActiveState=inactive' in state and 'MainPID=0' in state,state
print('REAL_HELPER_DIRECT_SENDER_RECOVERY_AND_STARTED_FENCED',flush=True)
print('RECOVERY_QUERY_READABLE_NO_NEW_START_OR_DEVICE_SERVICE',flush=True)
PY
