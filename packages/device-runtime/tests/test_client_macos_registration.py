import os
import plistlib
from types import SimpleNamespace

import pytest

from device_runtime import client_macos_registration as registration
from device_runtime.client_macos_layout import MacClientLayout
from device_runtime.client_macos_plist import publish_agent


@pytest.fixture
def account(tmp_path):
    layout = MacClientLayout(os.getuid(), tmp_path)
    layout.plist.parent.mkdir(parents=True)
    result = SimpleNamespace(name="test", layout=layout, gid=os.getgid())
    publish_agent(result)
    return result


def test_original_agent_is_valid(account):
    registration.validate_agent(account)


@pytest.mark.parametrize(
    "key,value",
    [
        ("KeepAlive", True),
        ("ProgramArguments", ["/bin/true"]),
        ("ExitTimeOut", 0),
        ("AbandonProcessGroup", False),
    ],
)
def test_modified_definition_refused(account, key, value):
    definition = account.layout.definition()
    definition[key] = value
    account.layout.plist.write_bytes(plistlib.dumps(definition))
    with pytest.raises(registration.MacRegistrationError, match="modifications"):
        registration.validate_agent(account)


@pytest.mark.parametrize("kind", ["symlink", "fifo", "missing", "public", "hardlink", "invalid"])
def test_invalid_agent_refused(account, kind):
    path = account.layout.plist
    if kind in ("symlink", "fifo", "missing"):
        path.unlink()
    if kind == "symlink":
        path.symlink_to(path.parent / "missing")
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "public":
        path.chmod(0o644)
    elif kind == "hardlink":
        os.link(path, path.parent / "alias")
    elif kind == "invalid":
        path.write_text("invalid")
    with pytest.raises(registration.MacRegistrationError):
        registration.validate_agent(account)


@pytest.mark.parametrize(
    "field,value", [("uid", 99999), ("gid", 99999), ("home", "/other"), ("user", "other")]
)
def test_registered_identity_is_rechecked(account, monkeypatch, field, value):
    identity = {
        "user": account.name,
        "uid": account.layout.uid,
        "gid": account.gid,
        "home": str(account.layout.home),
    }
    identity[field] = value
    monkeypatch.setattr(registration, "read_identity", lambda: identity)
    monkeypatch.setattr(registration, "resolve_account", lambda _: account)
    with pytest.raises(registration.MacRegistrationError, match="account changed"):
        registration.registered_account()


def test_integer_is_not_boolean_override(account):
    definition = account.layout.definition()
    definition["RunAtLoad"] = 1
    account.layout.plist.write_bytes(plistlib.dumps(definition))
    with pytest.raises(registration.MacRegistrationError, match="modifications"):
        registration.validate_agent(account)


def test_binary_plist_with_same_typed_definition_allowed(account):
    account.layout.plist.write_bytes(
        plistlib.dumps(account.layout.definition(), fmt=plistlib.FMT_BINARY)
    )
    registration.validate_agent(account)


@pytest.mark.parametrize("change", ["link", "mode"])
def test_agent_metadata_changed_during_parse_refused(account, monkeypatch, change):
    original = registration.plistlib.loads
    path = account.layout.plist

    def parse_and_change(data):
        value = original(data)
        if change == "link":
            import os

            os.link(path, path.with_suffix(".retained"))
        else:
            path.chmod(0o644)
        return value

    monkeypatch.setattr(registration.plistlib, "loads", parse_and_change)
    with pytest.raises(registration.MacRegistrationError):
        registration.validate_agent(account)
