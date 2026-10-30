#!/bin/sh
# Disposable Linux container only; source overlay, not production supervision.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = query-inherited-lock-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import json, os, signal, socket, tempfile, time
from pathlib import Path
from device_runtime import native_helper as h
with tempfile.TemporaryDirectory(prefix='device-inherit-',dir='/root') as root:
    registry=Path(root)/'registry'; registry.mkdir(mode=0o700)
    endpoint=Path(root)/'query.sock'
    with h._daemon_lease_pair(registry):
        child=os.fork()
        if child==0:
            try:
                h._serve_credential_queries_locked(registry,endpoint,group=0)
            except BaseException:
                os._exit(2)
            os._exit(3)
    try:
        end=time.monotonic()+5
        while not endpoint.exists() and time.monotonic()<end:
            time.sleep(.01)
        assert endpoint.exists(), 'child did not start listener'
        for acquire in (h._daemon_anchor,h._daemon_file_lease):
            try:
                with acquire(registry): raise AssertionError('inherited lock missing')
            except h.HelperError as exc: assert 'already active' in str(exc)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(5); client.connect(str(endpoint))
            client.sendall(b'{"action":"start","instance_id":"probe","operation_id":"one"}\n')
            assert json.loads(client.recv(4096))=={'ok':False,'error':'helper_request_refused'}
    finally:
        # The child is ours and remains unreaped, so its PID cannot be reused.
        os.kill(child,signal.SIGTERM)
        end=time.monotonic()+5
        while True:
            pid,status=os.waitpid(child,os.WNOHANG)
            if pid==child: break
            if time.monotonic()>=end:
                os.kill(child,signal.SIGKILL)
                pid,status=os.waitpid(child,0)
                raise RuntimeError('child failed to exit in time')
            time.sleep(.01)
        assert os.WIFSIGNALED(status) and os.WTERMSIG(status)==signal.SIGTERM
    with h._daemon_lease_pair(registry): pass
print('INHERITED_DAEMON_LOCKS_QUERY_LISTENER_START_REFUSED')
print('SOURCE_OVERLAY_NOT_SUPERVISION_OR_AUTHORIZATION')
PY
