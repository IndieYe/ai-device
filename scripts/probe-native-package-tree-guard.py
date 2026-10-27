"""Disposable Linux experiment for a candidate package-tree guard; not production."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from device_runtime import native_helper as helper
from device_runtime.native_prepare_sender import TREE_GUARD

ROOT = Path('/var/lib/device-package-tree-probe')
CHILD = """from pathlib import Path
import os, sys, time
Path(sys.argv[1]).write_text(str(os.getpid()))
deadline = time.monotonic() + 12
while not Path(sys.argv[2]).exists() and time.monotonic() < deadline:
 time.sleep(.02)
Path(sys.argv[3]).touch()
"""
PARENT = """import subprocess,sys
subprocess.Popen([sys.executable,'-c',sys.argv[1],*sys.argv[2:]],start_new_session=True)
"""


def wait_for(path, guard=None):
    deadline = time.monotonic() + 15
    while not path.exists():
        if guard is not None and guard.poll() is not None:
            raise RuntimeError(f'guard exited early: {guard.returncode}')
        if time.monotonic() >= deadline:
            raise TimeoutError(str(path))
        time.sleep(.02)


def run_case(name, crash):
    directory = ROOT / name
    directory.mkdir(mode=0o700)
    ready, release, done = (directory / s for s in ('ready', 'release', 'done'))
    receipt = directory / 'sender.json'
    intent = {'barrier': 'linux-subreaper-v1', 'transaction': name, 'snapshot': 'fixture'}
    with helper.package_lease(ROOT, exclusive=True) as fd:
        guard = subprocess.Popen(
            [sys.executable, '-I', '-B', '-c', TREE_GUARD, str(receipt), json.dumps(intent),
             'null', sys.executable, '-c', PARENT, CHILD, str(ready), str(release), str(done)],
            pass_fds=(fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    try:
        wait_for(ready, guard)
        pid = int(ready.read_text())
        assert Path(f'/proc/{pid}').exists()
        assert not receipt.exists() and guard.poll() is None
        try:
            with helper.package_lease(ROOT, exclusive=True):
                raise AssertionError('guard should retain package lease')
        except helper.HelperError:
            pass
        if crash:
            guard.kill()  # Only the child created by this disposable fixture.
            guard.wait(timeout=5)
            assert not receipt.exists() and not done.exists()
            # A lost guard can release the lock while an adopted descendant survives.
            with helper.package_lease(ROOT, exclusive=True):
                assert Path(f'/proc/{pid}').exists()
                assert not done.exists() and not receipt.exists()
            print('TREE_GUARD_CRASH_NO_RECEIPT_DESCENDANT_ALIVE_LOCK_FREE', flush=True)
        else:
            release.touch()
            guard.wait(timeout=15)
            assert guard.returncode == 0 and done.exists()
            assert not Path(f'/proc/{pid}').exists(), 'descendant was not reaped by guard'
            data = json.loads(receipt.read_text())
            assert data == {'state': 'process_tree_reaped', 'intent': intent, 'returncode': 0,
                            'descendants_confirmed': True, 'mechanism': 'linux-subreaper-v1'}
            print('TREE_GUARD_DESCENDANT_REAPED_BEFORE_RECEIPT', flush=True)
    finally:
        release.touch(exist_ok=True)
        wait_for(done)
        guard.wait(timeout=15)
        # PID 1 may adopt the child when the guard crashes. Reap it if so.
        pid = int(ready.read_text())
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    if crash:
        assert not receipt.exists()


def main():
    if (sys.platform != 'linux' or os.geteuid() != 0 or os.getpid() != 1
            or os.environ.get('DEVICE_NATIVE_DISPOSABLE_PROBE') != 'package-tree-v1'):
        raise RuntimeError('isolated Linux container PID1 required')
    ROOT.mkdir(mode=0o700)
    run_case('complete', False)
    run_case('crash', True)


if __name__ == '__main__':
    main()
