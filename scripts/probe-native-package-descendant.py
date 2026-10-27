"""Disposable Linux package guard probe; no actual package action or deployment."""
import json
import os
import sys
import time
from pathlib import Path

from device_runtime import native_bootstrap as bootstrap
from device_runtime import native_helper as helper
from device_runtime import native_update as update


def main():
    if (sys.platform != 'linux' or os.geteuid() != 0 or os.getpid() != 1
            or os.environ.get('DEVICE_NATIVE_DISPOSABLE_PROBE') != 'package-descendant-v1'):
        raise RuntimeError('isolated Linux container PID1 required')
    registry = Path('/var/lib/device-package-descendant-probe')
    registry.mkdir(mode=0o700)
    stem = registry / 'fixture'
    snapshot = stem.with_suffix('.bootstrap')
    bootstrap.snapshot_runtime(Path(sys.base_prefix), snapshot)
    intent = {'prepared_sha256': 'fixture', 'runtime_snapshot_sha256': helper._digest(snapshot / 'snapshot.json')}
    fake = Path('/usr/bin/dpkg')
    original = Path('/usr/bin/dpkg.fixture-original')
    if original.exists():
        raise RuntimeError('fixture cannot reuse altered dpkg')
    fake.rename(original)
    fake.write_text('#!/bin/sh\nsleep 8 &\necho $! > /var/lib/device-package-descendant-probe/child.pid\nexit 0\n')
    fake.chmod(0o755)
    receipt = stem.with_suffix('.sender.json')
    with helper.package_lease(registry, exclusive=True) as fd:
        update.Debian().apply(Path('/fixture-not-a-package'), lease_fd=fd, receipt_path=receipt, intent=intent)
    assert json.loads(receipt.read_bytes())['state'] == 'sender_reaped'
    pid = int((registry / 'child.pid').read_text())
    assert Path(f'/proc/{pid}').exists(), 'fixture descendant exited too early'
    with helper.package_lease(registry, exclusive=True):
        assert Path(f'/proc/{pid}').exists()
        print('PACKAGE_DIRECT_CHILD_RECEIPT_WITH_DESCENDANT_ALIVE_AND_LOCK_FREE', flush=True)
    # Wait naturally for the bounded fixture child; do not signal other processes.
    deadline = time.monotonic() + 20
    while Path(f'/proc/{pid}/stat').exists():
        fields = Path(f'/proc/{pid}/stat').read_text().split()
        if fields[2] == 'Z':
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            break
        if time.monotonic() >= deadline:
            raise TimeoutError('fixture descendant did not exit')
        time.sleep(.1)
    assert receipt.exists()


if __name__ == '__main__':
    main()
