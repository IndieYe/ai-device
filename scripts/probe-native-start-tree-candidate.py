"""Exercise an unwired guard with an inert sender in a disposable Linux PID namespace.

This replaces /usr/bin/systemctl inside that container only. No systemd or Device
is started. It does not test external delegation, durable recovery or production.
"""
import ast
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/run/start-tree-candidate')


def wait_for(predicate, label):
    deadline = time.monotonic() + 15
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError(label)
        time.sleep(0.02)


def lock_available(path):
    with path.open('r+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def main():
    if (sys.platform != 'linux' or os.getpid() != 1 or os.geteuid() != 0
            or os.environ.get('DEVICE_NATIVE_DISPOSABLE_PROBE') != 'start-tree-candidate-v1'):
        raise RuntimeError('disposable Linux root PID1 fixture required')
    source = Path('/source/native_start_sender.py').read_text()
    tree = ast.parse(source)
    guard = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'TREE_GUARD' for t in node.targets))
    ROOT.mkdir(mode=0o700)
    fake = Path('/usr/bin/systemctl')
    fake.write_text('''#!/bin/sh
set -eu
[ "$1" = start ]
d="/run/start-tree-candidate/$2"
touch /run/start-tree-candidate/action-called
(
 echo $$ > "$d/child-ready"
 while [ ! -e "$d/release" ]; do sleep 0.05; done
 touch "$d/done"
) </dev/null >/dev/null 2>&1 &
if [ "$2" = timeout ]; then
 while [ ! -e "$d/release" ]; do sleep 0.05; done
fi
exit 0
''')
    fake.chmod(0o755)
    intent = {'sender_protocol': 'systemd-start-tree-subreaper-v1', 'operation': 'preflight'}
    preflight = ROOT / 'preflight'
    preflight.mkdir()
    receipt = preflight / 'receipt.json'
    for invalid in ({}, {'sender_protocol': 'old'}, [], None):
        result = subprocess.run([sys.executable, '-I', '-B', '-c', guard,
                                 str(receipt), json.dumps(invalid), 'preflight'],
                                capture_output=True, timeout=10, check=False)
        assert result.returncode != 0 and b'start tree intent protocol required' in result.stderr
        assert not receipt.exists() and not (ROOT / 'action-called').exists()
    for symlink in (False, True):
        if symlink:
            receipt.symlink_to('missing')
        else:
            receipt.write_bytes(b'preserve')
        result = subprocess.run([sys.executable, '-I', '-B', '-c', guard,
                                 str(receipt), json.dumps(intent), 'preflight'],
                                capture_output=True, timeout=10, check=False)
        assert result.returncode != 0 and b'start tree receipt already exists' in result.stderr
        assert not (ROOT / 'action-called').exists()
        assert receipt.is_symlink() if symlink else receipt.read_bytes() == b'preserve'
        receipt.unlink()
    print('OLD_PROTOCOL_AND_EXISTING_RECEIPT_REFUSED_BEFORE_SENDER', flush=True)
    for name in ('normal', 'crash', 'timeout', 'dir-fsync-failure'):
        crash = name == 'crash'
        actual_guard = guard
        if name == 'timeout':
            assert 'timeout=120' in actual_guard
            actual_guard = actual_guard.replace('timeout=120', 'timeout=0.2')
        if name == 'dir-fsync-failure':
            target = 'os.fsync(fd); os.close(fd)'
            assert target in actual_guard
            actual_guard = actual_guard.replace(target, "raise OSError('injected directory fsync failure')")
        directory = ROOT / name
        directory.mkdir()
        receipt = directory / 'receipt.json'
        lease = directory / 'lease'
        lease.touch()
        fd = os.open(lease, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        intent = {'sender_protocol': 'systemd-start-tree-subreaper-v1', 'operation': name}
        process = subprocess.Popen([sys.executable, '-I', '-B', '-c', actual_guard,
                                    str(receipt), json.dumps(intent), name],
                                   pass_fds=(fd,), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE)
        os.close(fd)
        try:
            wait_for(lambda directory=directory: (directory / 'child-ready').exists(), 'descendant ready')
            assert process.poll() is None and not receipt.exists()
            assert not lock_available(lease)
            if name == 'timeout':
                time.sleep(0.5)
                assert process.poll() is None and not receipt.exists()
                assert not lock_available(lease)
            if crash:
                process.kill()  # Only the Popen-created guard in this disposable PID namespace.
                process.wait(timeout=10)
                assert not receipt.exists() and lock_available(lease)
                assert not (directory / 'done').exists()
                print('GUARD_CRASH_LOCK_FREE_DESCENDANT_LIVE_NO_RECEIPT', flush=True)
            (directory / 'release').touch()
            wait_for(lambda directory=directory: (directory / 'done').exists(), 'descendant done')
            if not crash:
                _, error = process.communicate(timeout=15)
                expected_code = 124 if name == 'timeout' else 0
                if name == 'dir-fsync-failure':
                    assert process.returncode != 0 and b'injected directory fsync failure' in error
                else:
                    assert process.returncode == expected_code, error.decode(errors='replace')
                assert lock_available(lease)
                assert json.loads(receipt.read_text()) == {
                    'schema_version': 1, 'state': 'start_process_tree_reaped',
                    'mechanism': 'linux-subreaper-v1', 'intent': intent, 'returncode': expected_code,
                }
                if name == 'normal':
                    print('TREE_RECEIPT_AFTER_DESCENDANT_EXIT_AND_LEASE_HELD', flush=True)
                elif name == 'timeout':
                    print('TIMED_OUT_SENDER_DESCENDANT_STILL_HOLDS_LEASE', flush=True)
                else:
                    print('VISIBLE_RECEIPT_AFTER_DIRECTORY_FSYNC_FAILURE_IS_UNKNOWN', flush=True)
        finally:
            (directory / 'release').touch(exist_ok=True)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if process.stderr:
                process.stderr.close()
    print('FIXTURE_ONLY_EXTERNAL_SYSTEMD_WORK_NOT_COVERED', flush=True)


if __name__ == '__main__':
    main()
