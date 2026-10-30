#!/bin/sh
# Disposable offline Linux only; never use on a real host.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = credential-frame-4a2542c ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 935787550b08eb79bd1c328557eae0b2f30340a64d838fa2444bcccaafe44a91 "$1" | sha256sum -c -
dpkg -i "$1"
P=/opt/ai-device/packages/0.2.2-native1
"$P/tools/uv" --no-config pip check --python "$P/runtime/bin/python3.13" --offline
"$P/runtime/bin/python3.13" -I -B - "$2" <<'PY'
import array
import os
import socket
import sys
import time
import zipfile
from pathlib import Path
from device_runtime import native_helper as helper

with zipfile.ZipFile(sys.argv[1]) as wheel:
    assert Path(helper.__file__).read_bytes() == wheel.read('device_runtime/native_helper.py')
print('INSTALLED_HELPER_MATCHES_WHEEL', flush=True)

# Listener enables SO_PASSCRED before the child connects and queues data.
path = '/tmp/credential-listener.sock'
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    listener.bind(path)
    listener.listen(1)
    ready_r, ready_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            listener.close()
            os.close(ready_r)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(path)
                client.sendall(b'prequeued\n')
                os.write(ready_w, b'x')
            os.close(ready_w)
            os._exit(0)
        except BaseException:
            os._exit(2)
    os.close(ready_w)
    assert os.read(ready_r, 1) == b'x'
    os.close(ready_r)
    with listener.accept()[0] as conn:
        frame, identity = helper._receive_root_credential_frame(conn, deadline=time.monotonic()+5)
        assert frame == b'prequeued\n' and identity == (pid, 0, 0), (frame, identity)
    assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0
print('PREQUEUED_ROOT_CREDENTIAL_OK', flush=True)

# A single child switches credentials between two forced recvmsg boundaries.
left, right = socket.socketpair()
left.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
ack_r, ack_w = os.pipe()
pid = os.fork()
if pid == 0:
    try:
        left.close()
        os.close(ack_w)
        right.sendall(b'first-')
        if os.read(ack_r, 1) != b'x':
            os._exit(2)
        os.setgroups([])
        os.setgid(12345)
        os.setuid(12345)
        right.sendall(b'second\n')
        right.close()
        os.close(ack_r)
        os._exit(0)
    except BaseException:
        os._exit(2)
right.close()
os.close(ack_r)
class AckConnection:
    def __init__(self, sock):
        self.sock = sock
        self.calls = 0
    def getsockopt(self, *args):
        return self.sock.getsockopt(*args)
    def settimeout(self, *args):
        return self.sock.settimeout(*args)
    def recvmsg(self, *args):
        result = self.sock.recvmsg(*args)
        self.calls += 1
        if self.calls == 1:
            os.write(ack_w, b'x')
        return result
try:
    try:
        helper._receive_root_credential_frame(AckConnection(left), deadline=time.monotonic()+5)
    except helper.HelperError as exc:
        assert 'sender changed or is not root' in str(exc), str(exc)
    else:
        raise AssertionError('mixed sender accepted')
    assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0
finally:
    left.close()
    os.close(ack_w)
print('MIXED_CREDENTIAL_FRAME_REFUSED', flush=True)

# Test actual descriptor delivery and ancillary truncation without descriptor leaks.
for count in (1, 80):
    left, right = socket.socketpair()
    left.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    fd = os.open('/dev/null', os.O_RDONLY)
    try:
        before = len(os.listdir('/proc/self/fd'))
        right.sendmsg([b'request\n'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]*count))])
        try:
            helper._receive_root_credential_frame(left, deadline=time.monotonic()+5)
        except helper.HelperError:
            pass
        else:
            raise AssertionError('unexpected descriptors accepted')
        assert len(os.listdir('/proc/self/fd')) == before
    finally:
        os.close(fd)
        left.close()
        right.close()
print('REAL_RIGHTS_AND_TRUNCATION_REFUSED_WITHOUT_FD_LEAK', flush=True)

left, right = socket.socketpair()
left.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
right.sendall(b'partial')
started = time.monotonic()
try:
    try:
        helper._receive_root_credential_frame(left, deadline=started+0.2)
    except helper.HelperError:
        pass
    else:
        raise AssertionError('partial frame accepted')
    assert time.monotonic()-started < 2
finally:
    left.close()
    right.close()
print('PARTIAL_FRAME_DEADLINE_REFUSED', flush=True)
print('UNWIRED_NO_HANDLER_OR_DISPATCH_AUTHORIZATION', flush=True)
PY
