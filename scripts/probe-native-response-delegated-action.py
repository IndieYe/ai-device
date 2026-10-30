"""Disposable systemd fixture: matching response sender is not action provenance."""
import os
import socket
import struct
import sys
import time
from pathlib import Path

ROOT = Path('/run/device-response-delegation-fixture')
ENDPOINT = ROOT / 'helper.sock'
CREDENTIALS = struct.Struct('3i')


def check_fixture():
    if (sys.platform != 'linux' or os.geteuid() != 0
            or os.environ.get('DEVICE_NATIVE_DISPOSABLE_PROBE') != 'response-delegation-v1'
            or Path('/proc/1/comm').read_text().strip() != 'systemd'):
        raise RuntimeError('isolated Linux systemd fixture required')


def serve():
    check_fixture()
    ROOT.mkdir(mode=0o700)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(ENDPOINT))
        listener.listen(1)
        connection, _ = listener.accept()
        with connection:
            if connection.recv(16) != b'fixture-action\n':
                raise RuntimeError('unexpected fixture request')
            child = os.fork()
            if child == 0:
                try:
                    (ROOT / 'action-pid').write_text(str(os.getpid()))
                    os._exit(0)
                except OSError:
                    os._exit(1)
            _, status = os.waitpid(child, 0)
            if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
                raise RuntimeError('fixture child failed')
            connection.sendall(b'fixture-complete\n')
        deadline = time.monotonic() + 30
        while not (ROOT / 'release').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('fixture release')
            time.sleep(.05)


def observe():
    check_fixture()
    from device_runtime import native_helper as helper

    deadline = time.monotonic() + 20
    while not ENDPOINT.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('fixture socket')
        time.sleep(.05)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            connection.connect(str(ENDPOINT))
            with helper.scoped_helper_unit_peer(connection) as peer:
                connection.sendall(b'fixture-action\n')
                data, ancillary, flags, _ = connection.recvmsg(128, socket.CMSG_SPACE(CREDENTIALS.size))
                assert not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                records = [value for level, kind, value in ancillary
                           if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
                assert data == b'fixture-complete\n' and len(records) == 1
                assert len(records[0]) == CREDENTIALS.size
                sender_pid, sender_uid, _ = CREDENTIALS.unpack(records[0])
                action_pid = int((ROOT / 'action-pid').read_text())
                assert sender_uid == 0 and sender_pid == peer['pid'] and action_pid != sender_pid
                assert peer['dispatch_allowed'] is False and peer['loaded_code_verified'] is False
            print('MATCHING_UNIT_RESPONSE_WITH_DISTINCT_FIXTURE_ACTION_PROCESS', flush=True)
            print('RESPONSE_CREDENTIALS_DO_NOT_PROVE_ACTION_PROVENANCE', flush=True)
    finally:
        (ROOT / 'release').touch()


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in {'serve', 'observe'}:
        raise SystemExit('expected serve or observe')
    (serve if sys.argv[1] == 'serve' else observe)()
