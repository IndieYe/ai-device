#!/bin/sh
# Only a disposable offline Linux container; never use a real host.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = child-handshake-e1ffb44 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 1e59690850fed8db58fe8c3784fadbd60368afcfb577fbbb67db5f1d59691813 "$1" | sha256sum -c -
dpkg -i "$1"
P=/opt/ai-device/packages/0.2.2-native1
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
"$P/tools/uv" --no-config pip check --python "$P/runtime/bin/python3.13" --offline
"$B" -I -B - "$2" <<'PY'
import array, os, select, socket, sys, time, zipfile
from pathlib import Path
from device_runtime import native_bootstrap as b
with zipfile.ZipFile(sys.argv[1]) as wheel:
    assert Path(b.__file__).read_bytes() == wheel.read('device_runtime/native_bootstrap.py')
assert hasattr(os,'pidfd_open')
print('BOOTSTRAP_MODULE_MATCHES_WHEEL',flush=True)

def scenario(mode):
    receiver,sender=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    receiver.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
    challenge=os.urandom(32)
    child=os.fork()
    if child==0:
        try:
            receiver.close()
            sender.settimeout(2)
            token=sender.recv(32)
            if token!=challenge: os._exit(3)
            if mode=='exited': os._exit(0)
            if mode=='timeout': time.sleep(0.6)
            elif mode=='descendant':
                descendant=os.fork()
                if descendant==0:
                    sender.sendall(b'READY:'+token)
                    os._exit(0)
                _,status=os.waitpid(descendant,0)
                if os.waitstatus_to_exitcode(status)!=0: os._exit(4)
            elif mode=='rights':
                fd=os.open('/dev/null',os.O_RDONLY)
                sender.sendmsg([b'READY:'+token],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))])
                os.close(fd)
            else:
                sender.sendall(b'READY:'+(b'Z'*32 if mode=='wrong' else token))
            time.sleep(0.7)
            sender.close()
            os._exit(0)
        except BaseException:
            os._exit(2)
    sender.close()
    pidfd=os.pidfd_open(child)
    try:
        receiver.sendall(challenge)
        if mode=='exited':
            poller=select.poll(); poller.register(pidfd,select.POLLIN)
            assert poller.poll(2000), 'child exit not observed'
        before=len(os.listdir('/proc/self/fd'))
        try:
            result=b._observe_child_handshake(receiver,pidfd,child,challenge,deadline=time.monotonic()+(0.2 if mode=='timeout' else 3))
        except b.BootstrapError as exc:
            if mode=='good': raise
            if mode=='wrong': assert 'differs' in str(exc),str(exc)
            if mode=='descendant': assert 'differs' in str(exc),str(exc)
            if mode=='exited': assert 'child exited' in str(exc),str(exc)
            if mode=='rights': assert 'record' in str(exc),str(exc)
        else:
            assert mode=='good' and result=='observed_not_authorized'
        assert len(os.listdir('/proc/self/fd'))==before
    finally:
        receiver.close()
        os.close(pidfd)
        waited,status=os.waitpid(child,0)
        assert waited==child and os.waitstatus_to_exitcode(status)==0,(mode,status)
    print('CHILD_HANDSHAKE_'+mode.upper()+'_OK',flush=True)

for mode in ('good','wrong','descendant','exited','timeout','rights'):
    scenario(mode)
print('HANDSHAKE_OBSERVATION_NOT_SUPERVISOR_OR_AUTHORIZATION',flush=True)
PY
