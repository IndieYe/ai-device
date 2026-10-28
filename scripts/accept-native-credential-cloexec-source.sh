#!/bin/sh
# Source-overlay experiment inside a disposable offline container only.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = credential-cloexec-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' d45bff032c4f5b985b706f7978edd54b2970b2247bf1b43db134516e0ccca7b7 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import array, fcntl, os, socket, subprocess, sys, time
from device_runtime import native_helper as h
left,right=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
left.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
source=os.open('/dev/null',os.O_RDONLY)
class Observe:
    def getsockopt(self,*args): return left.getsockopt(*args)
    def settimeout(self,*args): return left.settimeout(*args)
    def recvmsg(self,*args):
        result=left.recvmsg(*args)
        rights=[payload for level,kind,payload in result[1] if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS]
        assert len(rights)==1
        received=array.array('i'); received.frombytes(rights[0])
        assert len(received)==1
        fd=received[0]
        assert fcntl.fcntl(fd,fcntl.F_GETFD)&fcntl.FD_CLOEXEC
        code='import os,sys\ntry: os.fstat(int(sys.argv[1]))\nexcept OSError: sys.exit(0)\nsys.exit(1)'
        subprocess.run([sys.executable,'-I','-B','-c',code,str(fd)],close_fds=False,check=True,timeout=5)
        return result
try:
    before=len(os.listdir('/proc/self/fd'))
    right.sendmsg([b'{}\n'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[source]))])
    try:
        h._receive_root_credential_frame(Observe(),deadline=time.monotonic()+5)
        raise AssertionError('rights not rejected')
    except h.HelperError:
        pass
    assert len(os.listdir('/proc/self/fd'))==before
finally:
    os.close(source); left.close(); right.close()
print('CREDENTIAL_CLOEXEC_AT_RECEIPT_AND_EXEC_REFUSED_RIGHTS')
print('SOURCE_OVERLAY_NOT_PRODUCTION_OR_AUTHORIZATION')
PY
