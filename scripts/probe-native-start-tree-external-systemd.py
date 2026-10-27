"""Run the unwired start guard against an inert unit in disposable systemd only.

The receipt describes sender descendants, not completion of the systemd unit.
"""
import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/run/start-tree-external')
UNIT = 'device-start-tree-external-fixture.service'


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=20).stdout.strip()


def wait_file(path):
    deadline = time.monotonic() + 15
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(str(path))
        time.sleep(0.05)


def main():
    if (sys.platform != 'linux' or os.geteuid() != 0
            or os.environ.get('DEVICE_NATIVE_DISPOSABLE_PROBE') != 'start-tree-external-v1'
            or Path('/proc/1/comm').read_text().strip() != 'systemd'):
        raise RuntimeError('disposable root systemd container required')
    tree = ast.parse(Path('/source/native_start_sender.py').read_text())
    guard = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'TREE_GUARD' for t in node.targets))
    ROOT.mkdir(mode=0o700)
    worker = ROOT / 'worker'
    worker.write_text('''#!/bin/sh
set -eu
touch /run/start-tree-external/ready
while [ ! -e /run/start-tree-external/release ]; do sleep 0.1; done
touch /run/start-tree-external/done
''')
    worker.chmod(0o700)
    unit = Path('/run/systemd/system') / UNIT
    unit.write_text(f'[Unit]\nDescription=Disposable tree boundary fixture\n'
                    f'[Service]\nType=simple\nExecStart={worker}\n')
    intent = {'sender_protocol': 'systemd-start-tree-subreaper-v1', 'operation': 'fixture'}
    receipt = ROOT / 'receipt.json'
    command('/usr/bin/systemctl', 'daemon-reload')
    try:
        result = subprocess.run([sys.executable, '-I', '-B', '-c', guard,
                                 str(receipt), json.dumps(intent), UNIT],
                                capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
        wait_file(ROOT / 'ready')
        assert json.loads(receipt.read_text()) == {
            'schema_version': 1, 'state': 'start_process_tree_reaped',
            'mechanism': 'linux-subreaper-v1', 'intent': intent, 'returncode': 0,
        }
        pid = int(command('/usr/bin/systemctl', 'show', UNIT, '--property=MainPID', '--value'))
        invocation = command('/usr/bin/systemctl', 'show', UNIT, '--property=InvocationID', '--value')
        assert pid > 1 and len(invocation) == 32
        assert Path(f'/proc/{pid}').exists() and not (ROOT / 'done').exists()
        assert any(line == 'PPid:\t1' for line in Path(f'/proc/{pid}/status').read_text().splitlines())
        print('START_TREE_RECEIPT_WHILE_SYSTEMD_UNIT_INVOCATION_ALIVE', flush=True)
        print('SENDER_TREE_RECEIPT_DOES_NOT_PROVE_UNIT_TERMINAL', flush=True)
    finally:
        (ROOT / 'release').touch(exist_ok=True)
        if (ROOT / 'ready').exists():
            wait_file(ROOT / 'done')


if __name__ == '__main__':
    main()
