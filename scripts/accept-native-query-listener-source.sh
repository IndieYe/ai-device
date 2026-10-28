#!/bin/sh
# Disposable Linux source overlay; not an installed production endpoint.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = query-listener-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' d45bff032c4f5b985b706f7978edd54b2970b2247bf1b43db134516e0ccca7b7 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import json, os, select, signal, socket, subprocess, sys, tempfile, time
from pathlib import Path
from device_runtime import native_helper as h
with tempfile.TemporaryDirectory(prefix='device-query-',dir='/root') as root:
    registry=Path(root)/'registry'; registry.mkdir(mode=0o700)
    endpoint=Path(root)/'query.sock'
    code='from device_runtime import native_helper as h; import sys; h._serve_credential_queries(sys.argv[1],sys.argv[2],group=0)'
    process=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(registry),str(endpoint)],stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+5
        while not endpoint.exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.01)
        assert endpoint.exists() and process.poll() is None
        assert endpoint.stat().st_mode & 0o777 == 0o600
        for lock in (h._daemon_anchor,h._daemon_file_lease):
            try:
                with lock(registry): raise AssertionError('lock not held')
            except h.HelperError as exc: assert 'already active' in str(exc)
        for frame in (b'{"action":"query","instance_id":"missing","operation_id":"one"}\n',
                      b'{"action":"start","instance_id":"missing","operation_id":"one"}\n',
                      b'{"action":"query","action":"start","instance_id":"missing","operation_id":"one"}\n'):
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                client.settimeout(5); client.connect(str(endpoint)); client.sendall(frame)
                assert json.loads(client.recv(4096))=={'ok':False,'error':'helper_request_refused'}
                assert client.recv(1)==b''
        assert not list(registry.glob('op-*.json'))
    finally:
        if process.poll() is None: process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
        process.stderr.close()
    with h._daemon_lease_pair(registry): pass
print('REAL_PRIVATE_LISTENER_FRAMES_AND_DAEMON_LOCKS_OK')
print('SOURCE_OVERLAY_NO_REGISTERED_INSTANCE_OR_PRODUCTION_AUTHORIZATION')
PY
