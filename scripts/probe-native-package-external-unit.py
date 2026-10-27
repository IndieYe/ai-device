"""Show that the unwired subreaper receipt does not cover a systemd-launched unit.

Run only in a fresh disposable systemd container; replaces its dpkg with an
inert fixture. This does not run an actual package or exercise production dispatch.
"""
import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/run/device-package-external-unit-probe')
UNIT = 'device-package-external-fixture.service'


def wait_file(path):
    deadline = time.monotonic() + 20
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(str(path))
        time.sleep(.05)


def main():
    if (sys.platform != 'linux' or os.geteuid() != 0
            or os.environ.get('DEVICE_NATIVE_DISPOSABLE_PROBE') != 'package-external-unit-v1'
            or Path('/proc/1/comm').read_text().strip() != 'systemd'
            or Path('/opt/ai-device').exists()):
        raise RuntimeError('fresh disposable Linux systemd container required')
    ROOT.mkdir(mode=0o700)  # Never reuse a previous fixture.
    source = Path('/candidate.py').read_text()
    module = ast.parse(source)
    guard = next(ast.literal_eval(node.value) for node in module.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'TREE_GUARD'
                         for target in node.targets))
    dpkg = Path('/usr/bin/dpkg')
    backup = Path('/usr/bin/dpkg.external-fixture-original')
    if backup.exists():
        raise RuntimeError('fixture dpkg backup already exists')
    dpkg.rename(backup)
    dpkg.write_text('''#!/bin/sh
set -eu
/usr/bin/systemd-run --quiet --unit=device-package-external-fixture.service /bin/sh -c 'touch /run/device-package-external-unit-probe/started; while [ ! -e /run/device-package-external-unit-probe/release ]; do sleep 0.1; done; touch /run/device-package-external-unit-probe/done'
while [ ! -e /run/device-package-external-unit-probe/started ]; do sleep 0.1; done
''')
    dpkg.chmod(0o755)
    receipt = ROOT / 'fixture.sender.json'
    intent = {'sender_protocol': 'debian-package-tree-subreaper-v1', 'operation': 'fixture'}
    try:
        result = subprocess.run([sys.executable, '-I', '-B', '-c', guard, str(receipt),
                                 json.dumps(intent), str(ROOT)], timeout=30, check=False)
        assert result.returncode == 0
        value = json.loads(receipt.read_text())
        assert value == {'schema_version': 1, 'state': 'package_process_tree_reaped',
                         'mechanism': 'linux-subreaper-v1', 'intent': intent, 'returncode': 0}
        pid_text = subprocess.run(['/usr/bin/systemctl', 'show', UNIT, '--value', '--property=MainPID'],
                                  check=True, text=True, capture_output=True, timeout=10).stdout.strip()
        pid = int(pid_text)
        assert pid > 1 and Path(f'/proc/{pid}').exists()
        status = Path(f'/proc/{pid}/status').read_text()
        assert any(line == 'PPid:\t1' for line in status.splitlines())
        assert not (ROOT / 'done').exists()
        print('TREE_RECEIPT_EXISTS_WHILE_EXTERNAL_SYSTEMD_WORKER_ALIVE', flush=True)
        print('EXTERNAL_WORKER_NOT_ADOPTED_BY_SUBREAPER_NO_RECOVERY_AUTHORIZATION', flush=True)
    finally:
        (ROOT / 'release').touch()
        if (ROOT / 'started').exists():
            wait_file(ROOT / 'done')


if __name__ == '__main__':
    main()
