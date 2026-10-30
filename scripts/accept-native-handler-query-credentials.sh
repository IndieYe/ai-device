#!/bin/sh
# Fresh, network-isolated systemd container only. No real devices or deployment.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = handler-query-v1 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(cat /proc/1/comm)" = systemd ] || exit 2
[ "$#" = 1 ] || exit 2
[ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
pkg=$1
printf '%s  %s\n' 3f0c6b8601b0ddcfe8bec46929131642c4b2f66d88a5f71431aa01143e3327d6 "$pkg" | sha256sum -c -
dpkg -i "$pkg"
P=/opt/ai-device/packages/0.2.2-native1
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
useradd -m -u 12345 probe
runuser -u probe -- "$P/runtime/bin/python3.13" -I -B -m device_runtime.native_install --payload "$P" --root /home/probe/instance --instance-id probe --server-url http://127.0.0.1:9
mkdir -p /var/lib/ai-device/manager /run/device-query-probe
printf 'TEST_ONLY=yes\n' > /etc/device-query-fixture.env
chmod 600 /etc/device-query-fixture.env
"$B" -I -B - <<'PY'
import json, socket, struct, subprocess, time
from pathlib import Path
from device_runtime import native_helper as h
registry=Path('/var/lib/ai-device/manager')
endpoint=Path('/run/device-query-probe/helper.sock')
h.register(registry,Path('/etc/systemd/system'),instance_id='probe',root=Path('/home/probe/instance'),username='probe',environment_file='/etc/device-query-fixture.env')
unit=Path('/etc/systemd/system/ai-device-native-helper.service')
unit.write_text(h.render_helper_unit(bootstrap_python=Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13'),registry=registry,socket_path=endpoint,group=0))
subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True)
subprocess.run(['/usr/bin/systemctl','start','ai-device-native-helper.service'],check=True)
deadline=time.monotonic()+15
while not endpoint.exists():
 if time.monotonic()>deadline: raise TimeoutError('helper socket unavailable')
 time.sleep(.05)
with socket.socket(socket.AF_UNIX) as c:
 c.settimeout(10); c.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1); c.connect(str(endpoint))
 with h.scoped_helper_unit_peer(c) as scope:
  c.sendall(json.dumps({'action':'query','instance_id':'probe','operation_id':'query-one'}).encode()+b'\n')
  response,anc,flags,_=c.recvmsg(65536,socket.CMSG_SPACE(struct.calcsize('3i')))
  assert not flags & (socket.MSG_TRUNC|socket.MSG_CTRUNC) and response.endswith(b'\n')
  creds=[data for level,kind,data in anc if level==socket.SOL_SOCKET and kind==socket.SCM_CREDENTIALS]
  assert len(creds)==1 and len(creds[0])==struct.calcsize('3i')
  pid,uid,gid=struct.unpack('3i',creds[0]); result=json.loads(response)
  assert result['ok'] is True and result['result']['instance_id']=='probe'
  assert result['result']['operation_id']=='query-one' and result['result']['intent'] is None
  assert pid==scope['pid'] and uid==0 and not scope['dispatch_allowed']
  assert not (registry/'op-probe-query-one.json').exists()
  print('REAL_HELPER_QUERY_RESPONSE_CREDENTIALS_MATCH_UNIT_PEER',flush=True)
  print('QUERY_ONLY_NO_START_INTENT_AND_NO_DISPATCH_AUTHORIZATION',flush=True)
PY
