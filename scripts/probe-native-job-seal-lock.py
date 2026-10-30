"""Disposable Linux lock protocol probe; never a production recovery or worker."""
import json
import os
import select
import subprocess
import sys
from pathlib import Path

from device_runtime import native_helper as helper

REGISTRY = Path('/var/lib/device-seal-probe')


def locks():
    return helper.Helper(REGISTRY)._lease('probe')


def child(kind):
    with locks(), helper.package_lease(REGISTRY, exclusive=True):
        print('LOCKED', flush=True)
        if sys.stdin.readline() != 'release\n':
            raise RuntimeError('fixture control channel closed')
        if kind == 'recovery':
            state = 'sealed_after_action' if (REGISTRY / 'action.json').exists() else 'sealed'
            helper._save_new(REGISTRY / 'sealed.json', {'state': state})
            print('SEALED', flush=True)
        elif kind == 'worker':
            if (REGISTRY / 'sealed.json').exists():
                print('REFUSED', flush=True)
            else:
                helper._save_new(REGISTRY / 'action.json', {'state': 'action_done'})
                print('ACTION', flush=True)
        else:
            raise RuntimeError('unknown fixture')


def start(kind):
    process = subprocess.Popen(
        [sys.executable, '-I', '-B', __file__, kind],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    if not select.select([process.stdout], [], [], 20)[0]:
        process.stdin.close()
        process.wait(timeout=20)
        raise TimeoutError('fixture lock acquisition timed out')
    assert process.stdout.readline().strip() == 'LOCKED'
    return process


def finish(process, expected):
    process.stdin.write('release\n')
    process.stdin.flush()
    out, err = process.communicate(timeout=20)
    assert process.returncode == 0 and out.strip() == expected, (process.returncode, out, err)


def main():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('isolated Linux root required')
    if len(sys.argv) == 2:
        child(sys.argv[1])
        return
    mode = os.environ.get('DEVICE_NATIVE_DISPOSABLE_PROBE')
    if os.getpid() != 1 or mode not in ('seal-recovery-first', 'seal-worker-first'):
        raise RuntimeError('disposable PID 1 probe required')
    REGISTRY.mkdir(mode=0o700)  # refuse reuse of any existing registry
    kind = 'recovery' if mode == 'seal-recovery-first' else 'worker'
    process = start(kind)
    try:
        try:
            with locks(), helper.package_lease(REGISTRY, exclusive=True):
                raise AssertionError('independent process acquired held instance lock')
        except helper.HelperError as exc:
            assert 'instance management busy' in str(exc)
        try:
            with helper.package_lease(REGISTRY, exclusive=True):
                raise AssertionError('independent process acquired held package lock')
        except helper.HelperError as exc:
            assert 'package or start sender still executing' in str(exc)
        finish(process, 'SEALED' if kind == 'recovery' else 'ACTION')
    finally:
        if process.poll() is None:
            process.stdin.close()
            process.wait(timeout=20)
    if kind == 'recovery':
        later = start('worker')
        finish(later, 'REFUSED')
        assert not (REGISTRY / 'action.json').exists()
        assert json.loads((REGISTRY / 'sealed.json').read_bytes()) == {'state': 'sealed'}
        print('RECOVERY_FIRST_LATE_FIXTURE_WORKER_REFUSED', flush=True)
    else:
        later = start('recovery')
        finish(later, 'SEALED')
        assert json.loads((REGISTRY / 'action.json').read_bytes()) == {'state': 'action_done'}
        assert json.loads((REGISTRY / 'sealed.json').read_bytes()) == {'state': 'sealed_after_action'}
        print('WORKER_FIRST_RECOVERY_REOBSERVED_ACTION', flush=True)


if __name__ == '__main__':
    main()
