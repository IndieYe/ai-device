#!/bin/sh
# Only a disposable, network-isolated systemd container.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = query-listener-2cd3c17 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(cat /proc/1/comm)" = systemd ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
printf '%s  %s\n' c1218ed62b82428a735877684c826b596bafe81e3da0005479365ec84f368725 "$2" | sha256sum -c -
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
import json, os, signal, socket, subprocess, sys, time, zipfile
from pathlib import Path
from device_runtime import native_helper as h
with zipfile.ZipFile(sys.argv[1]) as wheel:
    assert Path(h.__file__).read_bytes() == wheel.read('device_runtime/native_helper.py')
registry=Path('/var/lib/ai-device/manager')
h.register(registry,Path('/etc/systemd/system'),instance_id='probe',root=Path('/home/probe/instance'),username='probe',environment_file='/etc/device-query-fixture.env')
subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True,timeout=15)
endpoint='/run/device-query-probe/query.sock'
code='from device_runtime import native_helper as h; import sys; h._serve_credential_queries(sys.argv[1],sys.argv[2],group=0)'
p=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(registry),endpoint],stderr=subprocess.PIPE)
try:
    deadline=time.monotonic()+10
    while not Path(endpoint).exists() and p.poll() is None and time.monotonic()<deadline: time.sleep(.05)
    assert p.poll() is None and Path(endpoint).exists()
    def exchange(frame):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(10); client.connect(endpoint); client.sendall(frame)
            chunks=[]
            while True:
                chunk=client.recv(65536)
                if not chunk: break
                chunks.append(chunk)
                assert sum(map(len,chunks))<=65536
            return json.loads(b''.join(chunks))
    response=exchange(b'{"action":"query","instance_id":"probe","operation_id":"one"}\n')
    assert response['ok'] is True,response
    assert response['result']['instance_id']=='probe' and response['result']['observation']['ActiveState']=='inactive',response
    for frame in (b'{"action":"start","instance_id":"probe","operation_id":"one"}\n',b'{"action":"query","action":"start","instance_id":"probe","operation_id":"one"}\n'):
        assert exchange(frame)=={'ok':False,'error':'helper_request_refused'}
    for lease in (h._daemon_anchor,h._daemon_file_lease):
        try:
            with lease(registry): raise AssertionError('listener lease missing')
        except h.HelperError as exc: assert 'already active' in str(exc)
    assert not list(registry.glob('op-probe-*.json'))
    status=subprocess.run(['/usr/bin/systemctl','show','ai-device-client-probe.service','--property=ActiveState,MainPID','--no-pager'],check=True,capture_output=True,text=True,timeout=10).stdout
    assert 'ActiveState=inactive' in status and 'MainPID=0' in status,status
    print('REAL_REGISTERED_INSTANCE_PRIVATE_QUERY_OK_START_REFUSED',flush=True)
finally:
    if p.poll() is None: p.send_signal(signal.SIGTERM)
    p.wait(timeout=10); p.stderr.close()
with h._daemon_lease_pair(registry): pass
print('DAEMON_LOCKS_RELEASED_FIXTURE_NOT_SUPERVISION_OR_AUTHORIZATION',flush=True)
PY
