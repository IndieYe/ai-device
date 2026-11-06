import os
import plistlib
from types import SimpleNamespace

import pytest

from device_runtime.client_macos_layout import MacClientLayout
from device_runtime.client_macos_plist import PlistPublishError, publish_agent


@pytest.fixture
def account(tmp_path):
    layout = MacClientLayout(os.getuid(), tmp_path)
    layout.plist.parent.mkdir(parents=True)
    return SimpleNamespace(layout=layout, gid=os.getgid())


def test_publish_complete_private_definition(account):
    path = publish_agent(account)
    assert plistlib.loads(path.read_bytes()) == account.layout.definition()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_nlink == 1
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_existing_definition_never_overwritten(account, kind):
    path = account.layout.plist
    if kind == "file":
        path.write_text("existing")
    else:
        path.symlink_to(path.parent / "missing")
    inode = path.lstat().st_ino
    with pytest.raises(PlistPublishError, match="preserved"):
        publish_agent(account)
    assert path.lstat().st_ino == inode
    assert list(path.parent.iterdir()) == [path]


def test_fsync_failure_never_publishes(account, monkeypatch):
    def fail(_):
        raise OSError("sensitive")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(PlistPublishError):
        publish_agent(account)
    assert list(account.layout.plist.parent.iterdir()) == []


def test_racing_registration_wins_without_overwrite(account, monkeypatch):
    original = os.link

    def race(source, destination, **kwargs):
        destination.write_text("other registration")
        original(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", race)
    with pytest.raises(PlistPublishError):
        publish_agent(account)
    assert account.layout.plist.read_text() == "other registration"
    assert len(list(account.layout.plist.parent.iterdir())) == 1


def test_public_destination_rejected_before_write(account):
    account.layout.plist.parent.chmod(0o777)
    with pytest.raises(PlistPublishError, match="directory"):
        publish_agent(account)
    assert list(account.layout.plist.parent.iterdir()) == []


def test_cleanup_failure_reports_published_and_preserves_definition(account, monkeypatch):
    from pathlib import Path

    original = Path.unlink

    def fail_temporary(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise PermissionError("cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_temporary)
    with pytest.raises(PlistPublishError, match="published; temporary cleanup failed"):
        publish_agent(account)
    assert plistlib.loads(account.layout.plist.read_bytes()) == account.layout.definition()
    assert account.layout.plist.stat().st_nlink == 2
