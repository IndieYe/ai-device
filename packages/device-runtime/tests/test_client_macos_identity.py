import json
import os
from types import SimpleNamespace

import pytest

from device_runtime.client_macos_identity import MacIdentityError, reserve_identity


@pytest.fixture
def setup(tmp_path):
    directory = tmp_path / "management"
    directory.mkdir(mode=0o700)
    account = SimpleNamespace(
        name="test",
        gid=os.getgid(),
        layout=SimpleNamespace(uid=os.getuid(), home=tmp_path / "home"),
    )
    return directory, account


def test_identity_is_private_and_complete(setup):
    directory, account = setup
    reserve_identity(account, directory=directory)
    path = directory / "registration.json"
    assert json.loads(path.read_text())["home"] == str(account.layout.home)
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(directory.iterdir()) == [path]


@pytest.mark.parametrize("existing", ["invalid", '{"uid":999}', ""])
def test_existing_record_never_overwritten(setup, existing):
    directory, account = setup
    path = directory / "registration.json"
    path.write_text(existing)
    inode = path.stat().st_ino
    with pytest.raises(MacIdentityError, match="reserved"):
        reserve_identity(account, directory=directory)
    assert path.read_text() == existing
    assert path.stat().st_ino == inode


def test_second_account_cannot_reserve(setup):
    directory, account = setup
    reserve_identity(account, directory=directory)
    account.name = "other"
    with pytest.raises(MacIdentityError):
        reserve_identity(account, directory=directory)
    assert json.loads((directory / "registration.json").read_text())["user"] == "test"


def test_failed_flush_does_not_publish(setup, monkeypatch):
    directory, account = setup

    def fail(_):
        raise OSError("failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        reserve_identity(account, directory=directory)
    assert list(directory.iterdir()) == []


def test_read_reserved_identity(setup):
    from device_runtime.client_macos_identity import read_identity

    directory, account = setup
    reserve_identity(account, directory=directory)
    assert read_identity(directory=directory)["uid"] == account.layout.uid


@pytest.mark.parametrize("content", ["{}", "not json", "[]", "x" * 8193])
def test_read_invalid_record_fails_closed(setup, content):
    from device_runtime.client_macos_identity import read_identity

    directory, _ = setup
    path = directory / "registration.json"
    path.touch(mode=0o600)
    path.write_text(content)
    with pytest.raises(MacIdentityError):
        read_identity(directory=directory)


@pytest.mark.parametrize(
    "field,value",
    [
        ("user", "a\nb"),
        ("user", "-root"),
        ("home", "/var/root"),
        ("home", "/"),
        ("home", "/Users/../root"),
        ("uid", True),
    ],
)
def test_malformed_account_values_rejected(setup, field, value):
    from device_runtime.client_macos_identity import read_identity

    directory, account = setup
    reserve_identity(account, directory=directory)
    path = directory / "registration.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(MacIdentityError):
        read_identity(directory=directory)


def test_public_management_directory_rejected(setup):
    from device_runtime.client_macos_identity import read_identity

    directory, account = setup
    reserve_identity(account, directory=directory)
    directory.chmod(0o755)
    with pytest.raises(MacIdentityError, match="directory"):
        read_identity(directory=directory)


def test_directory_alias_not_accepted(setup):
    from device_runtime.client_macos_identity import read_identity

    directory, account = setup
    reserve_identity(account, directory=directory)
    alias = directory.parent / "alias"
    alias.symlink_to(directory)
    with pytest.raises(MacIdentityError, match="directory"):
        read_identity(directory=alias)


@pytest.mark.parametrize("during_read", [False, True])
def test_read_identity_rejects_retained_publication_link(setup, monkeypatch, during_read):
    import device_runtime.client_macos_identity as identity

    directory, account = setup
    reserve_identity(account, directory=directory)
    path = directory / "registration.json"
    before = path.read_bytes()
    retained = directory / ".identity-interrupted.tmp"
    if during_read:
        original = identity.json.loads

        def load_and_link(data):
            value = original(data)
            os.link(path, retained)
            return value

        monkeypatch.setattr(identity.json, "loads", load_and_link)
    else:
        os.link(path, retained)
    with pytest.raises(MacIdentityError):
        identity.read_identity(directory=directory)
    assert path.stat().st_nlink == 2
    assert path.read_bytes() == before == retained.read_bytes()


def test_read_identity_rejects_mode_drift_during_inspection(setup, monkeypatch):
    """The post-read recheck must revalidate mode, not only dev/ino/nlink.

    Widening the permissions keeps the same inode, so an identity/dev/link-only
    comparison would still report the record as unchanged and return it.
    """
    import device_runtime.client_macos_identity as identity

    directory, account = setup
    reserve_identity(account, directory=directory)
    path = directory / "registration.json"
    original = identity.json.loads

    def load_then_widen(data):
        value = original(data)
        path.chmod(0o644)
        return value

    monkeypatch.setattr(identity.json, "loads", load_then_widen)
    with pytest.raises(MacIdentityError, match="changed during inspection"):
        identity.read_identity(directory=directory)
