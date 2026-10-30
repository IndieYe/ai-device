#!/bin/sh
# Destructive only to this explicitly disposable, network-isolated Linux container.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = helper-deadline-1a58f6d ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(cat /proc/1/comm)" = systemd ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' e801ab2cdcfb3a592c4cc829c62b137cc5c7c3bef89b1998949d7653faf369b3 "$1" | sha256sum -c -
dpkg -i "$1"
P=/opt/ai-device/packages/0.2.2-native1
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
useradd -m -u 12345 probe
runuser -u probe -- "$P/runtime/bin/python3.13" -I -B -m device_runtime.native_install --payload "$P" --root /home/probe/instance --instance-id probe --server-url http://127.0.0.1:9
mkdir -p /var/lib/ai-device/manager /run/device-helper-deadline
printf 'TEST_ONLY=yes\n' > /etc/device-helper-deadline.env
chmod 600 /etc/device-helper-deadline.env
"$B" -I -B - "$2" <<'PY'
import json, os, socket, subprocess, sys, time, zipfile
from pathlib import Path
from device_runtime import native_helper as h

wheel=Path(sys.argv[1])
with zipfile.ZipFile(wheel) as archive:
    expected=archive.read('device_runtime/native_helper.py')
    assert Path(h.__file__).read_bytes()==expected, h.__file__
    runtime=Path('/opt/ai-device/packages/0.2.2-native1/runtime/lib/python3.13/site-packages/device_runtime/native_helper.py')
    assert runtime.read_bytes()==expected, runtime
print('INSTALLED_BOOTSTRAP_AND_RUNTIME_HELPER_MATCH_WHEEL',flush=True)
registry=Path('/var/lib/ai-device/manager')
endpoint=Path('/run/device-helper-deadline/helper.sock')
h.register(registry,Path('/etc/systemd/system'),instance_id='probe',root=Path('/home/probe/instance'),username='probe',environment_file='/etc/device-helper-deadline.env')
unit=Path('/etc/systemd/system/ai-device-native-helper.service')
unit.write_text(h.render_helper_unit(bootstrap_python=Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13'),registry=registry,socket_path=endpoint,group=0))
subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True)
subprocess.run(['/usr/bin/systemctl','start','ai-device-native-helper.service'],check=True)
limit=time.monotonic()+15
while not endpoint.exists():
    if time.monotonic()>limit: raise TimeoutError('helper socket')
    time.sleep(.05)
request={'action':'query','instance_id':'probe','operation_id':'quick'}
first=h.call_helper(endpoint,request)
assert first['ok'] is True, first
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.settimeout(8)
s.connect(str(endpoint))
begin=time.monotonic()
# No complete frame; each fragment arrives well under the former per-read limit.
for _ in range(10):
    s.sendall(b' ')
    time.sleep(.45)
reply=b''
while not reply.endswith(b'\n'):
    chunk=s.recv(4096)
    if not chunk: raise AssertionError('closed without refusal')
    reply+=chunk
elapsed=time.monotonic()-begin
assert json.loads(reply)=={'ok':False,'error':'helper_request_refused'},reply
assert 4.5 <= elapsed < 8,elapsed
s.close()
second=h.call_helper(endpoint,{'action':'query','instance_id':'probe','operation_id':'after'})
assert second['ok'] is True,second
assert not (registry/'op-probe-quick.json').exists()
assert not (registry/'op-probe-after.json').exists()
state=subprocess.run(['/usr/bin/systemctl','show','ai-device-client-probe.service','--property=ActiveState,MainPID','--no-pager'],check=True,capture_output=True,text=True).stdout
assert 'ActiveState=inactive' in state and 'MainPID=0' in state,state
print(f'REAL_HELPER_SLOW_FRAME_REFUSED_SECONDS={elapsed:.3f}',flush=True)
print('FAST_QUERY_BEFORE_AND_AFTER_OK_DEVICE_INACTIVE',flush=True)
print('HELPER_UNIT_ACTIVE='+subprocess.check_output(['/usr/bin/systemctl','is-active','ai-device-native-helper.service'],text=True).strip(),flush=True)
PY
