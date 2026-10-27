"""Disposable fixture for the real start guard; never starts a service."""
import json
import os
import sys
import time
from pathlib import Path

from device_runtime import native_helper as h


def wait_for(path):
    deadline = time.monotonic() + 15
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(str(path))
        time.sleep(.02)


def main():
    if (sys.platform != 'linux' or os.geteuid() != 0 or os.getpid() != 1
            or os.environ.get('DEVICE_NATIVE_DISPOSABLE_PROBE') != 'start-sender-descendant-v1'):
        raise RuntimeError('disposable Linux PID1 fixture required')
    registry = Path('/run/device-start-sender-descendant')
    registry.mkdir(mode=0o700)
    sender = registry / 'fake-systemctl'
    child = registry / 'child.pid'
    release = registry / 'release'
    done = registry / 'done'
    sender.write_text(f'''#!/bin/sh
set -eu
(
 echo $$ > /dev/null
 while [ ! -e {release} ]; do sleep 0.05; done
 touch {done}
) >/dev/null 2>&1 </dev/null &
echo $! > {child}
''')
    sender.chmod(0o700)
    intent = {'action': 'start', 'instance_id': 'probe', 'operation_id': 'one',
              'unit_sha256': 'fixture', 'execution_barrier': 2}
    receipt = registry / 'sender-probe-one.json'
    helper = h.Helper(registry)
    try:
        with helper._execution_lease('probe') as execution_fd, h.package_lease(registry) as package_fd:
            h.Systemd(str(sender)).start('fixture.service', lease_fd=execution_fd,
                                         package_fd=package_fd, receipt_path=receipt, intent=intent)
        wait_for(child)
        pid = int(child.read_text())
        assert Path(f'/proc/{pid}').exists() and not done.exists()
        value = json.loads(receipt.read_text())
        assert h._valid_sender_completion(value, intent)
        with helper._execution_lease('probe', existing=True), h.package_lease(registry, exclusive=True):
            assert Path(f'/proc/{pid}').exists() and not done.exists()
        print('START_SENDER_RECEIPT_AND_LOCKS_FREE_WHILE_DESCENDANT_ALIVE', flush=True)
        release.touch()
        wait_for(done)
        print('START_SENDER_DESCENDANT_ACTED_AFTER_RECEIPT', flush=True)
    finally:
        release.touch(exist_ok=True)


if __name__ == '__main__':
    main()
