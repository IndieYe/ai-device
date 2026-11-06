import plistlib
from pathlib import Path

import pytest

from device_runtime.client_macos_layout import LABEL, MacClientLayout


@pytest.mark.parametrize("uid", [0, -1, True, "501", None])
def test_requires_explicit_non_root_uid(uid):
    with pytest.raises(ValueError, match="ordinary user"):
        MacClientLayout(uid, Path("/Users/test"))


@pytest.mark.parametrize(
    "home", [Path("relative"), Path("/"), Path("/var/root"), Path("/Users/../root"), "/Users/test"]
)
def test_rejects_ambiguous_home(home):
    with pytest.raises(ValueError):
        MacClientLayout(501, home)


def test_paths_use_only_selected_account(monkeypatch):
    monkeypatch.setenv("HOME", "/var/root")
    layout = MacClientLayout(502, Path("/Volumes/Test Home/alice"))
    assert layout.config == layout.directory / "client.json"
    assert layout.token == layout.directory / "token"
    assert layout.state == layout.directory / "state"
    assert layout.plist == layout.home / "Library/LaunchAgents" / f"{LABEL}.plist"
    assert layout.service == f"gui/502/{LABEL}"
    assert layout.domain == "gui/502"
    for path in (layout.config, layout.token, layout.state, layout.plist, layout.log):
        assert path.is_relative_to(layout.home)


def test_launchagent_roundtrip_uses_argv_not_shell():
    layout = MacClientLayout(501, Path("/Users/a b;$name"))
    data = plistlib.loads(layout.render())
    assert data == layout.definition()
    assert data["ProgramArguments"] == ["/opt/ai-device-client/bin/ai-device-client-launcher"]
    assert data["StandardOutPath"] == data["StandardErrorPath"] == str(layout.log)
    assert "UserName" not in data  # user GUI domain, not a root daemon
    assert "EnvironmentVariables" not in data
    assert data["Umask"] == 0o077


def test_definition_does_not_turn_stop_into_infinite_shutdown_wait():
    data = MacClientLayout(501, Path("/Users/test")).definition()
    assert "ExitTimeOut" not in data
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    assert data["RunAtLoad"] is True
    assert data["AbandonProcessGroup"] is True
    assert data["ThrottleInterval"] >= 30
    assert "PathState" not in data["KeepAlive"]
