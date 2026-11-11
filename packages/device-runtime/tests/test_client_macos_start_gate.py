import os
import select
import subprocess
import sys

import pytest

from device_runtime.client_macos_lock import (
    MacLockBusy,
    provision_runtime_lock,
    run_or_replace_lock,
)
from device_runtime.client_macos_start_gate import (
    prevent_start,
    provision_start_gate,
    startup_handoff,
)


@pytest.fixture
def directory(tmp_path):
    tmp_path.chmod(0o700)
    provision_start_gate(tmp_path, uid=os.getuid(), gid=os.getgid())
    provision_runtime_lock(tmp_path / "runtime.lock", uid=os.getuid(), gid=os.getgid())
    return tmp_path


def test_multiple_starters_exclude_maintenance(directory):
    with (
        startup_handoff(directory, uid=os.getuid()),
        startup_handoff(directory, uid=os.getuid()),
        pytest.raises(MacLockBusy),
        prevent_start(directory, uid=os.getuid()),
    ):
        pytest.fail("maintenance overlapped startup")


def test_gate_can_close_while_original_runtime_is_alive(directory):
    with (
        run_or_replace_lock(directory / "runtime.lock", uid=os.getuid()),
        prevent_start(directory, uid=os.getuid()),
        pytest.raises(MacLockBusy),
        startup_handoff(directory, uid=os.getuid()),
    ):
        pytest.fail("successor entered")


def test_handoff_retains_runtime_after_shared_gate_release(directory):
    with startup_handoff(directory, uid=os.getuid()):
        runtime = run_or_replace_lock(directory / "runtime.lock", uid=os.getuid())
        runtime.__enter__()
    try:
        with (
            prevent_start(directory, uid=os.getuid()),
            pytest.raises(MacLockBusy),
            run_or_replace_lock(directory / "runtime.lock", uid=os.getuid()),
        ):
            pytest.fail("original runtime lost lock")
    finally:
        runtime.__exit__(None, None, None)


def test_body_failure_releases_gate_without_removing_inode(directory):
    inode = (directory / "start.lock").stat().st_ino
    with pytest.raises(OSError, match="body"), prevent_start(directory, uid=os.getuid()):
        raise OSError("body")
    with startup_handoff(directory, uid=os.getuid()):
        assert (directory / "start.lock").stat().st_ino == inode


def test_missing_gate_is_not_created(tmp_path):
    with pytest.raises(RuntimeError), prevent_start(tmp_path, uid=os.getuid()):
        pytest.fail("missing gate accepted")
    assert not (tmp_path / "start.lock").exists()


def test_cross_process_successor_refused_without_runtime_access(directory):
    code = r"""
import os,sys
from pathlib import Path
from device_runtime.client_macos_start_gate import startup_handoff
from device_runtime.client_macos_lock import MacLockBusy
try:
    with startup_handoff(Path(sys.argv[1]), uid=os.getuid()):
        Path(sys.argv[2]).touch()
except MacLockBusy:
    raise SystemExit(75)
"""
    marker = directory / "loaded-runtime"
    with prevent_start(directory, uid=os.getuid()):
        result = subprocess.run(
            [sys.executable, "-c", code, str(directory), str(marker)],
            check=False,
            capture_output=True,
            timeout=5,
        )
        assert result.returncode == 75, result.stderr
        assert not marker.exists()
    result = subprocess.run(
        [sys.executable, "-c", code, str(directory), str(marker)],
        check=False,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert marker.exists()


def test_finite_owner_exit_releases_gate(directory):
    code = r"""
import os,sys,select
from pathlib import Path
from device_runtime.client_macos_start_gate import prevent_start
with prevent_start(Path(sys.argv[1]), uid=os.getuid()):
    print('HELD',flush=True)
    select.select([0],[],[],5)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(directory)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.readline() == b"HELD\n"
        with pytest.raises(MacLockBusy), startup_handoff(directory, uid=os.getuid()):
            pytest.fail("gate absent")
        _, errors = child.communicate(input=b"finish", timeout=7)
        assert child.returncode == 0, errors
        with startup_handoff(directory, uid=os.getuid()):
            pass
    finally:
        if child.poll() is None:
            child.communicate(input=b"finish", timeout=7)
        child.stdout.close()
        child.stderr.close()
