#!/bin/sh
# Disposable offline Linux container only; unsigned development package.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = bootstrap-scope-bcb6ec5 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' d45bff032c4f5b985b706f7978edd54b2970b2247bf1b43db134516e0ccca7b7 "$1" | sha256sum -c -
printf '%s  %s\n' 15c355aa8addb7f450820a7858807dc8321ff9f2318dcd68b72df073c8a4d665 "$2" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
P=/opt/ai-device/packages/0.2.2-native1
"$P/tools/uv" --no-config pip check --python "$P/runtime/bin/python3.13" --offline
mkdir -p /var/lib/device-scope-probe/bootstrap-candidates /run/device-scope-probe
chmod 700 /var/lib/device-scope-probe /var/lib/device-scope-probe/bootstrap-candidates /run/device-scope-probe
"$B/bin/python3.13" -I -B - "$2" <<'PY'
import json, os, pathlib, signal, socket, subprocess, sys, time, zipfile
from device_runtime import native_bootstrap as b, native_helper as h
with zipfile.ZipFile(sys.argv[1]) as wheel:
    assert pathlib.Path(b.__file__).read_bytes() == wheel.read('device_runtime/native_bootstrap.py')
    assert pathlib.Path(h.__file__).read_bytes() == wheel.read('device_runtime/native_helper.py')
registry=pathlib.Path('/var/lib/device-scope-probe')
sockpath=pathlib.Path('/run/device-scope-probe/helper.sock')
source=pathlib.Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime')
result=b.publish_managed_helper_candidate(registry,source,'candidate')
assert b.read_helper_candidate(registry/'bootstrap-candidates','candidate') == result
assert result['activated'] is False
sha=result['snapshot_sha256']
args=[sys.executable,'-I','-B','-m','device_runtime.native_bootstrap','--registry',str(registry),'--socket',str(sockpath),'--group','0','--version','candidate','--snapshot-sha256']
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
            assert json.loads(connection.recv(1024))['ok'] is False
    print('EXACT_PACKAGE_CANDIDATE_SCOPE_AND_REFUSAL_OK',flush=True)
    print('NOT_SUPERVISION_OR_AUTHORIZATION',flush=True)
finally:
    if p.poll() is None:
        p.send_signal(signal.SIGTERM)
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        raise RuntimeError('test helper did not stop; container must exit')
    if p.stderr:
        p.stderr.close()
PY
