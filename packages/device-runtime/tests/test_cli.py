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
