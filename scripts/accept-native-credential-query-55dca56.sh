#!/bin/sh
# Disposable offline systemd container only; never use on a real host.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = credential-query-55dca56 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(cat /proc/1/comm)" = systemd ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' fad5a3ccfdf05478918a42f75cf3e2b22877e62aff23f416c0539a8df9d73b00 "$1" | sha256sum -c -
dpkg -i "$1"
P=/opt/ai-device/packages/0.2.2-native1
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
"$P/tools/uv" --no-config pip check --python "$P/runtime/bin/python3.13" --offline
useradd -m -u 12345 probe
runuser -u probe -- "$P/runtime/bin/python3.13" -I -B -m device_runtime.native_install --payload "$P" --root /home/probe/instance --instance-id probe --server-url http://127.0.0.1:9
mkdir -p /var/lib/ai-device/manager /run/device-query-probe
printf 'TEST_ONLY=yes\n' > /etc/device-query-fixture.env
chmod 600 /etc/device-query-fixture.env
"$B" -I -B - "$2" <<'PY'
import json, os, socket, subprocess, sys, time, zipfile
from pathlib import Path
from device_runtime import native_helper as h
with zipfile.ZipFile(sys.argv[1]) as wheel:
    assert Path(h.__file__).read_bytes() == wheel.read('device_runtime/native_helper.py')
print('BOOTSTRAP_HELPER_MATCHES_WHEEL', flush=True)
registry=Path('/var/lib/ai-device/manager')
root=Path('/home/probe/instance')
h.register(registry,Path('/etc/systemd/system'),instance_id='probe',root=root,username='probe',environment_file='/etc/device-query-fixture.env')
subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True,timeout=15)
service=h.Helper(registry)
class Observed:
    def __init__(self): self.calls=[]
    def handle(self, request, *, peer_uid):
        self.calls.append((request, peer_uid))
        return service.handle(request, peer_uid=peer_uid)
observed=Observed()
endpoint='/run/device-query-probe/query.sock'
with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
    listener.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
    listener.bind(endpoint)
    listener.listen(4)
    def exchange(frame):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(endpoint)
            client.sendall(frame)
            conn,_=listener.accept()
            h._serve_credential_query_once(observed,conn,deadline=time.monotonic()+5)
            result=client.recv(65536)
            assert client.recv(1)==b''
            return json.loads(result)
    req=b'{"action":"query","instance_id":"probe","operation_id":"one"}\n'
    response=exchange(req)
    assert response['ok'] is True,response
    assert response['result']['instance_id']=='probe'
    assert response['result']['observation']['ActiveState']=='inactive'
    assert len(observed.calls)==1 and observed.calls[0][1]==0
    for frame in (b'{"action":"start","instance_id":"probe","operation_id":"one"}\n',
                  b'{"action":"query","action":"start","instance_id":"probe","operation_id":"one"}\n'):
        assert exchange(frame)=={'ok':False,'error':'helper_request_refused'}
        assert len(observed.calls)==1
assert not list(registry.glob('op-probe-*.json'))
status=subprocess.run(['/usr/bin/systemctl','show','ai-device-client-probe.service','--property=ActiveState,MainPID','--no-pager'],check=True,capture_output=True,text=True,timeout=10).stdout
assert 'ActiveState=inactive' in status and 'MainPID=0' in status,status
print('REAL_SOCKET_ROOT_QUERY_REAL_HELPER_SYSTEMD_INACTIVE_OK',flush=True)
print('START_AND_DUPLICATE_FIELDS_REFUSED_BEFORE_HANDLER',flush=True)
print('PRIVATE_FIXTURE_NOT_PRODUCTION_LISTENER_OR_INVOCATION_PROOF',flush=True)
PY
