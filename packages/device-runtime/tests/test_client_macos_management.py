import os
from types import SimpleNamespace

import pytest

from device_runtime.client_macos_layout import MacClientLayout
from device_runtime.client_macos_management import (
    MacManagementError,
    ensure_directory,
    management_lock,
    prepare_user_directories,
)


def test_management_conflict_and_stable_inode(tmp_path):
    directory = tmp_path / "management"
    args = {"owner_uid": os.getuid(), "owner_gid": os.getgid()}
    with management_lock(directory, **args):
        inode = (directory / "management.lock").stat().st_ino
        with pytest.raises(MacManagementError, match="active"), management_lock(directory, **args):
            pytest.fail("concurrent management")
    with management_lock(directory, **args):
        assert (directory / "management.lock").stat().st_ino == inode


def test_management_preserves_operation_error(tmp_path):
    args = {"owner_uid": os.getuid(), "owner_gid": os.getgid()}
    with (
        pytest.raises(OSError, match="operation"),
        management_lock(tmp_path / "management", **args),
    ):
        raise OSError("operation")
    with management_lock(tmp_path / "management", **args):
        pass


@pytest.mark.parametrize("kind", ["public", "symlink", "file"])
def test_existing_bad_directory_not_repaired(tmp_path, kind):
    path = tmp_path / "directory"
    if kind == "public":
        path.mkdir(mode=0o755)
    elif kind == "file":
        path.write_text("preserve")
    else:
        path.symlink_to(tmp_path)
    inode = path.lstat().st_ino
    with pytest.raises(MacManagementError):
        ensure_directory(path, uid=os.getuid(), gid=os.getgid())
    assert path.lstat().st_ino == inode


def test_prepare_only_declared_user_directories(tmp_path):
    layout = MacClientLayout(os.getuid(), tmp_path)
    layout.directory.mkdir(parents=True, mode=0o700)
    sentinel = layout.directory / "token"
    sentinel.write_text("preserved")
    before = sentinel.stat()
    account = SimpleNamespace(layout=layout, gid=os.getgid())
    prepare_user_directories(account)
    prepare_user_directories(account)
    assert layout.state.is_dir()
    assert layout.log.parent.is_dir()
    assert layout.plist.parent.is_dir()
    assert not layout.plist.exists()
    assert not layout.log.exists()
    assert sentinel.stat().st_ino == before.st_ino
    assert sentinel.read_text() == "preserved"


def test_yielded_management_fd_matches_until_path_replaced(tmp_path):
    from device_runtime.client_macos_management import management_lock_matches

    directory = tmp_path / "management"
    with management_lock(directory, owner_uid=os.getuid(), owner_gid=os.getgid()) as fd:
        assert not os.get_inheritable(fd)
        assert management_lock_matches(fd, directory, owner_uid=os.getuid())
        path = directory / "management.lock"
        path.rename(directory / "retained.lock")
        path.touch(mode=0o600)
        assert not management_lock_matches(fd, directory, owner_uid=os.getuid())
        assert (directory / "retained.lock").exists()
    assert not management_lock_matches(fd, directory, owner_uid=os.getuid())


@pytest.mark.parametrize("change", ["mode", "link", "parent"])
def test_management_fd_recheck_rejects_changed_private_shape(tmp_path, change):
    from device_runtime.client_macos_management import management_lock_matches

    directory = tmp_path / "management"
    with management_lock(directory, owner_uid=os.getuid(), owner_gid=os.getgid()) as fd:
        path = directory / "management.lock"
        if change == "mode":
            path.chmod(0o644)
        elif change == "link":
            os.link(path, directory / "extra.lock")
        else:
            directory.chmod(0o755)
        assert not management_lock_matches(fd, directory, owner_uid=os.getuid())
