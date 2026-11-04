import json
from types import SimpleNamespace

import pytest

from device_runtime import client_conflicts as m


@pytest.fixture
def discovery(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    proc = tmp_path / "proc"
    proc.mkdir()
    units = tmp_path / "units"
    units.mkdir()
    monkeypatch.setattr(m, "_service_pid", lambda: 999999)
    monkeypatch.setattr(m, "PROC", proc)
    monkeypatch.setattr(m, "UNIT_DIRS", (units,))
    monkeypatch.setattr(m, "LEGACY_DIRS", (tmp_path / "legacy",))
    monkeypatch.setattr(m, "OWN_UNIT", units / "ai-device-client.service")
    monkeypatch.setattr(m.pwd, "getpwall", lambda: [SimpleNamespace(pw_dir=str(home))])
    return home, proc, units


def test_no_conflicts(discovery):
    m.check_conflicts()


@pytest.mark.parametrize(
    "roles,blocked", [({"client": {}}, True), ({"center": {}, "mcp": {}}, False)]
)
def test_default_instance_roles(discovery, roles, blocked):
    home, _, _ = discovery
    config = home / ".device-runtime/instances/old/config/roles.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(roles))
    if blocked:
        with pytest.raises(m.ClientConflict, match="legacy client instance"):
            m.check_conflicts()
    else:
        m.check_conflicts()


@pytest.mark.parametrize(
    "argv",
    [
        [
            "/custom/venv/bin/python",
            "-m",
            "device_runtime.cli",
            "client",
            "--device-id",
            "private-marker",
        ],
        ["/custom/python", "-m", "device_runtime.client_cli", "--config", "/custom/client.json"],
    ],
)
def test_custom_running_client_detected(discovery, argv):
    _, proc, _ = discovery
    p = proc / "999999"
    p.mkdir()
    (p / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
    with pytest.raises(m.ClientConflict, match="another running client") as error:
        m.check_conflicts()
    assert "private-marker" not in str(error.value)


def test_own_running_service_allowed(discovery):
    _, proc, _ = discovery
    p = proc / "999999"
    p.mkdir()
    argv = [m.OWN_PYTHON, "-I", "-m", "device_runtime.client_cli", "--config", m.OWN_CONFIG]
    (p / "cmdline").write_bytes("\0".join(argv).encode())
    m.check_conflicts()


@pytest.mark.parametrize("symlink", [False, True])
def test_custom_service_detected(discovery, tmp_path, symlink):
    _, _, units = discovery
    target = tmp_path / "custom.service" if symlink else units / "old.service"
    target.write_text("[Service]\nExecStart=/custom/run client\n")
    if symlink:
        (units / "old.service").symlink_to(target)
    with pytest.raises(m.ClientConflict, match="another client service"):
        m.check_conflicts()


def test_unknown_process_read_fails_closed(discovery, monkeypatch):
    _, proc, _ = discovery
    (proc / "999999").mkdir()
    original = m._read

    def denied(path, limit=65536):
        if path.name == "cmdline":
            raise PermissionError("PRIVATE")
        return original(path, limit)

    monkeypatch.setattr(m, "_read", denied)
    with pytest.raises(m.ClientConflict, match="inspection failed") as error:
        m.check_conflicts()
    assert "PRIVATE" not in str(error.value)


def test_same_command_outside_owned_service_is_conflict(discovery, monkeypatch):
    test_own_running_service_allowed(discovery)
    monkeypatch.setattr(m, "_service_pid", lambda: 1234)
    with pytest.raises(m.ClientConflict, match="running client"):
        m.check_conflicts()


def test_fifo_config_fails_without_blocking(discovery):
    import os

    home, _, _ = discovery
    config = home / ".device-runtime/instances/old/config/roles.json"
    config.parent.mkdir(parents=True)
    os.mkfifo(config)
    with pytest.raises(m.ClientConflict, match="regular files"):
        m.check_conflicts()


@pytest.mark.parametrize(
    "directory", ["/etc/systemd/user", "/usr/lib/systemd/user", "/run/systemd/user"]
)
def test_global_user_service_paths_are_in_discovery_contract(directory):
    from pathlib import Path

    assert Path(directory) in m.UNIT_DIRS
