import os
import subprocess
import sys

import pytest

from device_runtime.client_macos_lock import MacLockBusy, MacLockError, run_or_replace_lock


@pytest.fixture
def lock_path(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "runtime.lock"
    path.touch(mode=0o600)
    return path


def test_conflict_and_release_preserve_inode(lock_path):
    inode = lock_path.stat().st_ino
    with (
        run_or_replace_lock(lock_path, uid=os.getuid()),
        pytest.raises(MacLockBusy),
        run_or_replace_lock(lock_path, uid=os.getuid()),
    ):
        pytest.fail("concurrent acquisition")
    with run_or_replace_lock(lock_path, uid=os.getuid()):
        assert lock_path.stat().st_ino == inode


def test_exception_releases_lock(lock_path):
    with (
        pytest.raises(ValueError, match="operation"),
        run_or_replace_lock(lock_path, uid=os.getuid()),
    ):
        raise ValueError("operation")
    with run_or_replace_lock(lock_path, uid=os.getuid()):
        pass


@pytest.mark.parametrize("kind", ["missing", "symlink", "fifo", "public", "hardlink"])
def test_bad_lock_is_rejected(lock_path, kind):
    if kind in ("missing", "symlink", "fifo"):
        lock_path.unlink()
    if kind == "symlink":
        target = lock_path.parent / "target"
        target.touch(mode=0o600)
        lock_path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(lock_path, 0o600)
    elif kind == "public":
        lock_path.chmod(0o644)
    elif kind == "hardlink":
        os.link(lock_path, lock_path.parent / "alias")
    with pytest.raises(MacLockError), run_or_replace_lock(lock_path, uid=os.getuid()):
        pytest.fail("invalid lock accepted")


def test_real_child_conflict_then_abnormal_exit_releases(lock_path):
    # Finite child waits for one byte; parent always closes stdin, never kills.
    code = """
import os, sys
from pathlib import Path
from device_runtime.client_macos_lock import run_or_replace_lock
with run_or_replace_lock(Path(sys.argv[1]), uid=os.getuid()):
    print('locked', flush=True)
    sys.stdin.buffer.read(1)
    os._exit(7)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        import select

        ready, _, _ = select.select([child.stdout], [], [], 5)
        assert ready and child.stdout.readline().strip() == "locked"
        with pytest.raises(MacLockBusy), run_or_replace_lock(lock_path, uid=os.getuid()):
            pytest.fail("child lock not observed")
    finally:
        child.stdin.close()
        child.wait(timeout=5)
        child.stdout.close()
    assert child.returncode == 7
    with run_or_replace_lock(lock_path, uid=os.getuid()):
        pass


def test_operation_oserror_is_not_misreported_as_lock_failure(lock_path):
    with (
        pytest.raises(OSError, match="operation IO"),
        run_or_replace_lock(lock_path, uid=os.getuid()),
    ):
        raise OSError("operation IO")
    with run_or_replace_lock(lock_path, uid=os.getuid()):
        pass


def test_exec_child_does_not_inherit_lock(lock_path):
    # Verify CLOEXEC using a short-lived child, not an unbounded service.
    with run_or_replace_lock(lock_path, uid=os.getuid()):
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
            stdin=subprocess.PIPE,
            close_fds=False,
        )
    try:
        with run_or_replace_lock(lock_path, uid=os.getuid()):
            assert child.poll() is None
    finally:
        child.stdin.close()
        child.wait(timeout=5)


def test_provision_once_preserves_active_lock(lock_path):
    from device_runtime.client_macos_lock import provision_runtime_lock

    lock_path.unlink()
    assert provision_runtime_lock(lock_path, uid=os.getuid(), gid=os.getgid()) is True
    inode = lock_path.stat().st_ino
    with run_or_replace_lock(lock_path, uid=os.getuid()):
        assert provision_runtime_lock(lock_path, uid=os.getuid(), gid=os.getgid()) is False
        with pytest.raises(MacLockBusy), run_or_replace_lock(lock_path, uid=os.getuid()):
            pytest.fail("provisioning replaced active lock")
    assert lock_path.stat().st_ino == inode
    assert lock_path.stat().st_mode & 0o777 == 0o600


def test_provision_existing_invalid_permissions_not_repaired(lock_path):
    from device_runtime.client_macos_lock import provision_runtime_lock

    lock_path.write_text("preserve")
    lock_path.chmod(0o644)
    inode = lock_path.stat().st_ino
    with pytest.raises(MacLockError):
        provision_runtime_lock(lock_path, uid=os.getuid(), gid=os.getgid())
    assert lock_path.read_text() == "preserve"
    assert lock_path.stat().st_ino == inode
    assert lock_path.stat().st_mode & 0o777 == 0o644


def test_provision_failure_retains_inode_for_explicit_repair(lock_path, monkeypatch):
    from device_runtime.client_macos_lock import provision_runtime_lock

    lock_path.unlink()

    def fail(*args):
        raise OSError("private detail")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(MacLockError, match="provisioning failed"):
        provision_runtime_lock(lock_path, uid=os.getuid(), gid=os.getgid())
    assert lock_path.exists()


def test_provision_then_managed_entry(tmp_path):
    from types import SimpleNamespace

    from device_runtime.client_macos_entry import run_managed
    from device_runtime.client_macos_lock import provision_runtime_lock

    directory = tmp_path / "registration"
    directory.mkdir(mode=0o700)
    lock = directory / "runtime.lock"
    provision_runtime_lock(lock, uid=os.getuid(), gid=os.getgid())
    account = SimpleNamespace(
        layout=SimpleNamespace(
            uid=os.getuid(), directory=directory, config=directory / "client.json"
        )
    )
    called = []
    run_managed(account.layout.config, account=account, invoke=lambda argv: called.append(argv))
    assert called == [["--config", str(account.layout.config)]]


@pytest.mark.parametrize("uid,gid", [(0, 20), (-1, 20), (True, 20), (501, -1), (501, True)])
def test_provision_rejects_invalid_identity_before_create(lock_path, uid, gid):
    from device_runtime.client_macos_lock import provision_runtime_lock

    lock_path.unlink()
    with pytest.raises(MacLockError, match="ordinary account"):
        provision_runtime_lock(lock_path, uid=uid, gid=gid)
    assert not lock_path.exists()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink"])
def test_provision_rejects_special_existing_lock(lock_path, kind):
    from device_runtime.client_macos_lock import provision_runtime_lock

    if kind == "hardlink":
        os.link(lock_path, lock_path.parent / "alias")
    else:
        lock_path.unlink()
        if kind == "fifo":
            os.mkfifo(lock_path, 0o600)
        else:
            lock_path.symlink_to(lock_path.parent / "missing")
    with pytest.raises(MacLockError):
        provision_runtime_lock(lock_path, uid=os.getuid(), gid=os.getgid())
    assert os.path.lexists(lock_path)
