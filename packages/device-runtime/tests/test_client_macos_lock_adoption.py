import fcntl
import os

import pytest

from device_runtime.client_macos_lock import MacLockError, adopt_runtime_lock


@pytest.fixture
def lock(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "runtime.lock"
    path.touch(mode=0o600)
    return path


def opened(path, held=True):
    fd = os.open(path, os.O_RDWR)
    os.set_inheritable(fd, True)
    if held:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_adoption_retains_lock_and_closes_on_exit(lock):
    fd = opened(lock)
    with adopt_runtime_lock(lock, uid=os.getuid(), descriptor=fd):
        assert not os.get_inheritable(fd)
        with lock.open("r+b") as probe, pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(OSError):
        os.fstat(fd)
    with lock.open("r+b") as probe:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_unheld_descriptor_rejected(lock):
    fd = opened(lock, False)
    with (
        pytest.raises(MacLockError, match="not held"),
        adopt_runtime_lock(lock, uid=os.getuid(), descriptor=fd),
    ):
        pytest.fail("accepted unheld lock")
    with pytest.raises(OSError):
        os.fstat(fd)


def test_other_owner_does_not_prove_inherited_ownership(lock):
    owner = opened(lock)
    fd = opened(lock, False)
    try:
        with pytest.raises(MacLockError), adopt_runtime_lock(lock, uid=os.getuid(), descriptor=fd):
            pytest.fail("accepted another owner")
    finally:
        os.close(owner)


@pytest.mark.parametrize("change", ["mode", "owner", "path", "parent"])
def test_identity_mismatch_rejected(lock, change):
    fd = opened(lock)
    uid = os.getuid()
    if change == "mode":
        lock.chmod(0o644)
    elif change == "owner":
        uid += 1
    elif change == "path":
        lock = lock.with_name("missing")
    else:
        lock.parent.chmod(0o755)
    with pytest.raises(MacLockError), adopt_runtime_lock(lock, uid=uid, descriptor=fd):
        pytest.fail("invalid identity")
    with pytest.raises(OSError):
        os.fstat(fd)


def test_operation_exception_preserved(lock):
    fd = opened(lock)
    with (
        pytest.raises(OSError, match="body"),
        adopt_runtime_lock(lock, uid=os.getuid(), descriptor=fd),
    ):
        raise OSError("body")
    with pytest.raises(OSError):
        os.fstat(fd)


@pytest.mark.parametrize("fd", [True, -1, 0, 1, 2])
def test_standard_descriptors_never_consumed(lock, fd):
    with pytest.raises(MacLockError), adopt_runtime_lock(lock, uid=os.getuid(), descriptor=fd):
        pytest.fail("invalid descriptor")
