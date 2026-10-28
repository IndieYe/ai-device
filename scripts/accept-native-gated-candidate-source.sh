#!/bin/sh
# Single-use, network-isolated Linux container only; never a production supervisor.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = gated-candidate-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 3 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
cp "$3" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import json, os, socket, tempfile, time
from pathlib import Path
from device_runtime import native_bootstrap as b, native_helper as h
with tempfile.TemporaryDirectory(prefix='device-gated-', dir='/root') as root:
    registry=Path(root)/'registry'; registry.mkdir(mode=0o700)
    (registry/'bootstrap-candidates').mkdir(mode=0o700)
    endpoint=Path(root)/'query.sock'
    source=Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime')
    evidence=b.publish_managed_helper_candidate(registry,source,'candidate')
    contents=b._verified_helper_bytes(registry/'bootstrap-candidates','candidate',evidence['snapshot_sha256'])
    parent,child_sock=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    parent.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
    challenge=os.urandom(32)
    with h._daemon_lease_pair(registry):
        child=os.fork()
        if child==0:
            try:
                parent.close(); child_sock.settimeout(8)
                token=child_sock.recv(32)
                if len(token)!=32: os._exit(2)
                child_sock.sendall(b'READY:'+token)
                if child_sock.recv(2)!=b'GO': os._exit(3)
                child_sock.close()
                with b._verified_helper_modules(contents) as candidate:
                    candidate._serve_credential_queries_locked(registry,endpoint,group=0)
            except BaseException:
                os._exit(4)
            os._exit(5)
        child_sock.close()
        pidfd=os.pidfd_open(child)
        reaped=False
        try:
            parent.sendall(challenge)
            assert b._observe_child_handshake(parent,pidfd,child,challenge,deadline=time.monotonic()+5)=='observed_not_authorized'
            assert not endpoint.exists(), 'listener started before gate'
            parent.sendall(b'GO')
            end=time.monotonic()+5
            while not endpoint.exists() and b._handshake_pidfd_alive(pidfd) and time.monotonic()<end:
                time.sleep(.01)
            assert endpoint.exists() and b._handshake_pidfd_alive(pidfd)
            for lease in (h._daemon_anchor,h._daemon_file_lease):
                try:
                    with lease(registry): raise AssertionError('daemon lock missing')
                except h.HelperError as exc: assert 'already active' in str(exc)
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                client.settimeout(5); client.connect(str(endpoint))
                client.sendall(b'{"action":"start","instance_id":"probe","operation_id":"one"}\n')
                assert json.loads(client.recv(4096))=={'ok':False,'error':'helper_request_refused'}
            pid,code,status=b._reap_direct_child_pidfd(pidfd,deadline=time.monotonic()+5)
            reaped=True
            assert pid==child and code==os.CLD_KILLED
        finally:
            parent.close()
            if not reaped:
                b._reap_direct_child_pidfd(pidfd,deadline=time.monotonic()+5)
            os.close(pidfd)
    with h._daemon_lease_pair(registry): pass
    # A second direct child never receives GO; closing its channel must prevent
    # the candidate from loading or creating the listener.
    denied_endpoint=Path(root)/'denied.sock'
    parent,child_sock=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    parent.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
    challenge=os.urandom(32)
    with h._daemon_lease_pair(registry):
        child=os.fork()
        if child==0:
            try:
                parent.close(); child_sock.settimeout(5)
                token=child_sock.recv(32)
                if len(token)!=32: os._exit(2)
                child_sock.sendall(b'READY:'+token)
                if child_sock.recv(2)!=b'GO': os._exit(0)
                with b._verified_helper_modules(contents) as candidate:
                    candidate._serve_credential_queries_locked(registry,denied_endpoint,group=0)
            except BaseException:
                os._exit(4)
            os._exit(5)
        child_sock.close()
        pidfd=os.pidfd_open(child)
        try:
            parent.sendall(challenge)
            assert b._observe_child_handshake(parent,pidfd,child,challenge,deadline=time.monotonic()+5)=='observed_not_authorized'
            parent.close()
            end=time.monotonic()+5
            while b._handshake_pidfd_alive(pidfd) and time.monotonic()<end: time.sleep(.01)
            assert not b._handshake_pidfd_alive(pidfd), 'closed gate did not terminate child'
            pid,code,status=b._reap_direct_child_pidfd(pidfd,deadline=time.monotonic()+5)
            assert (pid,code,status)==(child,os.CLD_EXITED,0)
            assert not denied_endpoint.exists()
        finally:
            parent.close(); os.close(pidfd)
    with h._daemon_lease_pair(registry): pass
print('VERIFIED_BYTES_GATED_CHILD_PIDFD_CREDENTIALS_LOCKED_QUERY_REFUSED')
print('CLOSED_GATE_CHILD_EXITED_WITHOUT_LISTENER')
print('SOURCE_OVERLAY_FIXTURE_NOT_PRODUCTION_SUPERVISOR_OR_AUTHORIZATION')
PY
