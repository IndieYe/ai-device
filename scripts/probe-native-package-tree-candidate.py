"""Run an unwired package-tree guard against fake dpkg in a disposable container."""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from device_runtime import native_helper as helper

ROOT = Path('/var/lib/device-package-tree-candidate')


def wait_for(path, process=None):
    deadline = time.monotonic() + 20
    while not path.exists():
        if process is not None and process.poll() is not None:
            raise RuntimeError(f'guard exited before fixture ready: {process.returncode}')
        if time.monotonic() >= deadline:
            raise TimeoutError(str(path))
        time.sleep(.02)


def run_case(candidate, name, crash):
    directory = ROOT / name
    directory.mkdir(mode=0o700)
    receipt = directory / 'fixture.sender.json'
    release, done = directory / 'release', directory / 'done'
    intent = {'sender_protocol': candidate.PROTOCOL, 'operation': name, 'snapshot': 'fixture'}
    with helper.package_lease(ROOT, exclusive=True) as fd:
        guard = subprocess.Popen(
            [sys.executable, '-I', '-B', '-c', candidate.TREE_GUARD, str(receipt),
             json.dumps(intent), str(directory)], pass_fds=(fd,),
        )
    try:
        child_file = directory / 'child.pid'
        wait_for(child_file, guard)
        pid = int(child_file.read_text())
        assert Path(f'/proc/{pid}').exists() and not receipt.exists()
        try:
            with helper.package_lease(ROOT, exclusive=True):
                raise AssertionError('guard lost package lease')
        except helper.HelperError:
            pass
        if crash:
            guard.kill()  # Only the process created by this fixture.
            guard.wait(timeout=5)
            with helper.package_lease(ROOT, exclusive=True):
                assert Path(f'/proc/{pid}').exists() and not receipt.exists()
            print('CANDIDATE_GUARD_CRASH_NO_RECEIPT_CHILD_ALIVE_LOCK_FREE', flush=True)
        else:
            release.touch()
            assert guard.wait(timeout=20) == 7
            assert done.exists() and not Path(f'/proc/{pid}').exists()
            assert (directory / 'fixture.package.log').read_text().strip() == 'descendant-log'
            value = json.loads(receipt.read_text())
            assert value == {
                'schema_version': 1, 'state': 'package_process_tree_reaped',
                'mechanism': 'linux-subreaper-v1', 'intent': intent, 'returncode': 7,
            }
            assert candidate.valid_tree_receipt(value, intent)
            assert not candidate.valid_tree_receipt(value, {**intent, 'operation': 'other'})
            print('CANDIDATE_TREE_GUARD_REAPS_FAKE_DPKG_DESCENDANT', flush=True)
            before = receipt.read_bytes()
            (directory / 'called').unlink()
            with helper.package_lease(ROOT, exclusive=True) as fd:
                repeated = subprocess.run(
                    [sys.executable, '-I', '-B', '-c', candidate.TREE_GUARD, str(receipt),
                     json.dumps(intent), str(directory)], check=False, pass_fds=(fd,), capture_output=True,
                )
            assert repeated.returncode != 0 and not (directory / 'called').exists()
            assert receipt.read_bytes() == before
            print('CANDIDATE_EXISTING_RECEIPT_REFUSES_BEFORE_DPKG', flush=True)
    finally:
        release.touch(exist_ok=True)
        wait_for(done)
        guard.wait(timeout=20)
        pid = int((directory / 'child.pid').read_text())
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    if crash:
        assert not receipt.exists()


def main():
    if (sys.platform != 'linux' or os.geteuid() != 0 or os.getpid() != 1
            or os.environ.get('DEVICE_NATIVE_DISPOSABLE_PROBE') != 'package-tree-candidate-v1'):
        raise RuntimeError('isolated Linux container PID1 required')
    ROOT.mkdir(mode=0o700)
    spec = importlib.util.spec_from_file_location('device_runtime.native_package_sender', '/candidate.py')
    candidate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidate)
    fake = Path('/usr/bin/dpkg')
    original = Path('/usr/bin/dpkg.fixture-original')
    if original.exists():
        raise RuntimeError('fixture cannot reuse altered dpkg')
    fake.rename(original)
    fake.write_text('''#!/bin/sh
touch "$2/called"
(
 n=0
 while [ ! -e "$2/release" ] && [ "$n" -lt 400 ]; do sleep 0.05; n=$((n+1)); done
 echo descendant-log
 touch "$2/done"
) &
echo $! > "$2/child.pid"
exit 7
''')
    fake.chmod(0o755)
    bad = ROOT / 'bad-protocol'
    bad.mkdir(mode=0o700)
    with helper.package_lease(ROOT, exclusive=True) as fd:
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-c', candidate.TREE_GUARD,
             str(bad / 'fixture.sender.json'), json.dumps({'sender_protocol': 'old'}), str(bad)],
            check=False, pass_fds=(fd,), capture_output=True,
        )
    assert result.returncode != 0 and not (bad / 'called').exists()
    assert not (bad / 'fixture.sender.json').exists()
    print('CANDIDATE_PROTOCOL_DRIFT_REFUSES_BEFORE_DPKG', flush=True)
    for kind in ('file', 'dangling-symlink'):
        blocked = ROOT / ('orphan-log-' + kind)
        blocked.mkdir(mode=0o700)
        log = blocked / 'fixture.package.log'
        if kind == 'file':
            log.write_bytes(b'preserve')
        else:
            log.symlink_to('missing')
        blocked_receipt = blocked / 'fixture.sender.json'
        intent = {'sender_protocol': candidate.PROTOCOL, 'operation': kind, 'snapshot': 'fixture'}
        with helper.package_lease(ROOT, exclusive=True) as fd:
            result = subprocess.run(
                [sys.executable, '-I', '-B', '-c', candidate.TREE_GUARD,
                 str(blocked_receipt), json.dumps(intent), str(blocked)],
                check=False, pass_fds=(fd,), capture_output=True,
            )
        assert result.returncode != 0 and not (blocked / 'called').exists()
        assert not blocked_receipt.exists()
        if kind == 'file':
            assert log.read_bytes() == b'preserve'
        else:
            assert log.is_symlink() and os.readlink(log) == 'missing'
    print('CANDIDATE_ORPHAN_LOG_REFUSES_BEFORE_DPKG', flush=True)
    run_case(candidate, 'complete', False)
    run_case(candidate, 'crash', True)
    # Model failure after link but before the parent directory is durably synced.
    # This runs a modified copy of the candidate only inside this test process.
    directory = ROOT / 'dir-fsync-failure'
    directory.mkdir(mode=0o700)
    (directory / 'release').touch()
    receipt = directory / 'fixture.sender.json'
    intent = {'sender_protocol': candidate.PROTOCOL, 'operation': 'dir-fsync-failure', 'snapshot': 'fixture'}
    original_guard = candidate.TREE_GUARD
    fragment = 'os.fsync(fd); os.close(fd)'
    assert original_guard.count(fragment) == 1
    injected_guard = original_guard.replace(fragment, "raise OSError('injected directory fsync failure')")
    with helper.package_lease(ROOT, exclusive=True) as fd:
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-c', injected_guard, str(receipt),
             json.dumps(intent), str(directory)], check=False, pass_fds=(fd,), capture_output=True,
        )
    assert result.returncode != 0 and b'injected directory fsync failure' in result.stderr
    assert receipt.exists() and (directory / 'done').exists()
    print('CANDIDATE_VISIBLE_RECEIPT_DIR_FSYNC_FAILURE_IS_UNKNOWN', flush=True)


if __name__ == '__main__':
    main()
