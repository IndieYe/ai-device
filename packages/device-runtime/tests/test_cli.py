import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "mode", [[], ["client"], ["server"], ["bundle"], ["bundle", "build"], ["bundle", "inspect"]]
)
def test_cli_help(mode):
    result = subprocess.run(
        [sys.executable, "-m", "device_runtime.cli", *mode, "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0 and "usage:" in result.stdout


def test_invalid_server_configuration_does_not_echo_environment(tmp_path):
    sentinel = "not-real-credential-private-test-value"
    env = {
        **os.environ,
        "DEVICE_RUNTIME_ADMIN_TOKEN": sentinel,
        "DEVICE_RUNTIME_DEVICE_TOKENS": sentinel,
    }
    result = subprocess.run(
        [sys.executable, "-m", "device_runtime.cli", "server", "--state-dir", str(tmp_path)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert sentinel not in result.stdout + result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("entry", ["device_runtime.cli", "device_runtime.client_cli"])
def test_client_help_does_not_load_legacy_installation(entry):
    # Run without the test process's already-imported modules masking dependencies.
    code = """
import importlib.abc
import runpy
import sys
class RejectLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {
            'device_runtime.installation', 'device_runtime.bundle',
            'device_runtime.upgrader', 'device_runtime.native_update',
        }:
            raise AssertionError('client imported legacy management: ' + fullname)
sys.meta_path.insert(0, RejectLegacy())
entry = sys.argv[1]
sys.argv = [entry] + (['client'] if entry.endswith('.cli') else []) + ['--help']
runpy.run_module(entry, run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, entry],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--state-dir" in result.stdout
    assert "--max-active-tasks" in result.stdout


def test_client_run_path_without_legacy_management(tmp_path):
    code = """
import importlib.abc
import runpy
import sys
class RejectLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'device_runtime.installation', 'device_runtime.bundle',
                        'device_runtime.upgrader', 'device_runtime.native_update'}:
            raise AssertionError('legacy management imported: ' + fullname)
sys.meta_path.insert(0, RejectLegacy())
from device_runtime.client import DeviceClient
async def probe(self):
    assert self.device_id == 'probe-one'
    assert self.executor.device_id == 'probe-one'
    await self.close()
DeviceClient.run = probe
sys.argv = ['device-runtime', 'client', '--server-url', 'ws://localhost:8765',
            '--device-id', 'probe-one', '--state-dir', sys.argv[1]]
runpy.run_module('device_runtime.cli', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        env={**os.environ, "DEVICE_RUNTIME_TOKEN": "test-only-private-probe-token"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
