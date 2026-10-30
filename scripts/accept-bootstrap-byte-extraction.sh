#!/bin/sh
# Disposable, network-isolated Linux container only; no production device state.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] || exit 2
pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' 909d10088315e1e64bfb9fdaad264ee41c6b71797d1032e43752c53a494c1d2a "$pkg" | sha256sum -c -
dpkg -i "$pkg"
py=/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
base=/opt/ai-device/bootstrap/0.2.2-native1/runtime/lib/python3.13/site-packages/device_runtime
printf '%s  %s\n' d596e3ee06f1248df048a0cb5447d06e3cb87c20a3f1952e620a48f58aa7fbd1 "$base/native_bootstrap.py" | sha256sum -c -
mkdir -p /var/lib/device-byte-probe/bootstrap-candidates /run/device-byte-probe
chmod 700 /var/lib/device-byte-probe /var/lib/device-byte-probe/bootstrap-candidates /run/device-byte-probe
"$py" -I -B - <<'PY'
import json, os, pathlib, signal, socket, subprocess, time
from device_runtime import native_bootstrap as b, native_helper as h
registry=pathlib.Path('/var/lib/device-byte-probe')
sockpath=pathlib.Path('/run/device-byte-probe/helper.sock')
source=pathlib.Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime')
result=b.publish_managed_helper_candidate(registry,source,'candidate')
assert b.read_helper_candidate(registry/'bootstrap-candidates','candidate') == result
assert result['activated'] is False
sha=result['snapshot_sha256']
py='/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13'
args=[py,'-I','-B','-m','device_runtime.native_bootstrap','--registry',str(registry),'--socket',str(sockpath),'--group','0','--version','candidate','--snapshot-sha256']
bad=subprocess.run(args+['0'*64],capture_output=True,text=True,timeout=30)
assert bad.returncode == 1 and 'candidate snapshot differs from unit' in bad.stderr
assert not sockpath.exists()
p=subprocess.Popen(args+[sha],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
try:
    end=time.monotonic()+30
    while not sockpath.exists() and p.poll() is None and time.monotonic()<end:
        time.sleep(.1)
    assert p.poll() is None and sockpath.exists(), 'candidate did not start'
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(5)
        connection.connect(str(sockpath))
        with h._socket_peer_process(connection) as peer:
            assert peer['pid'] == p.pid
            connection.sendall(b'{}\n')
            reply=connection.recv(1024)
            assert json.loads(reply)['ok'] is False
    print('ACTUAL_BOOTSTRAP_OK snapshot='+sha+' peer_matches_started_pid=true dispatch_not_authorized=true',flush=True)
finally:
    if p.poll() is None:
        p.send_signal(signal.SIGTERM)
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        raise RuntimeError('test helper did not stop; container must exit')
PY
