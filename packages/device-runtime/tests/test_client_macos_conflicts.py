import json
import os
import plistlib

import pytest

from device_runtime.client_macos_conflicts import MacClientConflict, check_known_files


def test_absent_defaults_allowed(tmp_path):
    check_known_files([tmp_path / "home"], service_directories=[tmp_path / "system"])


@pytest.mark.parametrize(
    "roles,conflict", [({"client": {}}, True), ({"center": {}}, False), ([], True)]
)
def test_default_instance_roles(tmp_path, roles, conflict):
    config = tmp_path / ".device-runtime/instances/old/config/roles.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(roles))
    if conflict:
        with pytest.raises(MacClientConflict):
            check_known_files([tmp_path], service_directories=[])
    else:
        check_known_files([tmp_path], service_directories=[])


@pytest.mark.parametrize(
    "definition",
    [
        {
            "Label": "com.device-runtime.old",
            "ProgramArguments": ["/bin/sh", "/custom/launchd/run-client.sh"],
        },
        {"Label": "custom", "ProgramArguments": ["/python", "-m", "device_runtime.client_cli"]},
        {"Label": "custom", "ProgramArguments": ["/bin/sh", "/custom/launchd/run-client.sh"]},
        {"Label": "custom", "ProgramArguments": ["/custom/run", "client"]},
        {"Label": "local.ai-device.client"},
        {"Label": "custom", "Program": "/opt/ai-device-client/bin/ai-device-client-launcher"},
        {
            "Label": "custom",
            "ProgramArguments": ["/opt/ai-device-client/bin/ai-device-client-launcher"],
        },
    ],
)
def test_client_services_rejected(tmp_path, definition):
    directory = tmp_path / "Library/LaunchAgents"
    directory.mkdir(parents=True)
    (directory / "custom.plist").write_bytes(plistlib.dumps(definition))
    with pytest.raises(MacClientConflict, match="service detected"):
        check_known_files(iter([tmp_path]), service_directories=[])


def test_unrelated_service_preserved(tmp_path):
    file = tmp_path / "other.plist"
    content = plistlib.dumps({"Label": "other", "ProgramArguments": ["/usr/bin/true"]})
    file.write_bytes(content)
    check_known_files([], service_directories=[tmp_path])
    assert file.read_bytes() == content


@pytest.mark.parametrize("kind", ["fifo", "invalid", "dangling"])
def test_unreadable_definition_fails_closed(tmp_path, kind):
    path = tmp_path / "service.plist"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "invalid":
        path.write_text("not a plist")
    else:
        path.symlink_to(tmp_path / "missing")
    with pytest.raises(MacClientConflict):
        check_known_files([], service_directories=[tmp_path])


def test_symlinked_definition_inspected(tmp_path):
    directory = tmp_path / "services"
    directory.mkdir()
    target = tmp_path / "target"
    target.write_bytes(plistlib.dumps({"Label": "com.device-runtime.old"}))
    (directory / "linked.plist").symlink_to(target)
    with pytest.raises(MacClientConflict, match="service detected"):
        check_known_files([], service_directories=[directory])


def test_own_registration_is_not_reported_as_another_service(tmp_path):
    """The installed client's own plist must not read as a rival installation.

    It is still refused -- registration is create-only -- but naming it
    "another client service" sends the operator hunting for a second client
    that does not exist.
    """
    home = tmp_path / "home"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    own = agents / "local.ai-device.client.plist"
    own.write_bytes(
        plistlib.dumps(
            {
                "Label": "local.ai-device.client",
                "ProgramArguments": ["/opt/ai-device-client/bin/ai-device-client-launcher"],
            }
        )
    )
    with pytest.raises(MacClientConflict, match="already registered for this user"):
        check_known_files([home], service_directories=[], own_plist=own)


def test_foreign_plist_same_directory_still_conflicts(tmp_path):
    """Excluding our own path must not blind the check to a rival definition."""
    home = tmp_path / "home"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    own = agents / "local.ai-device.client.plist"
    (agents / "local.ai-device.client-other.plist").write_bytes(
        plistlib.dumps(
            {
                "Label": "local.ai-device.client",
                "ProgramArguments": ["/opt/ai-device-client/bin/ai-device-client-launcher"],
            }
        )
    )
    with pytest.raises(MacClientConflict, match="another client service"):
        check_known_files([home], service_directories=[], own_plist=own)


def test_own_plist_label_mismatch_still_conflicts(tmp_path):
    """Path alone is not identity: an unexpected label at our path is a conflict."""
    home = tmp_path / "home"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    own = agents / "local.ai-device.client.plist"
    own.write_bytes(
        plistlib.dumps(
            {
                "Label": "com.device-runtime.other",
                "ProgramArguments": ["/opt/ai-device-client/bin/ai-device-client-launcher"],
            }
        )
    )
    with pytest.raises(MacClientConflict, match="another client service"):
        check_known_files([home], service_directories=[], own_plist=own)


@pytest.mark.parametrize(
    "argv",
    [
        ["/python", "-m", "device_runtime.client_cli"],
        ["/python", "-m", "device_runtime.client_macos_entry"],
        ["/python", "-m", "device_runtime.cli", "client"],
        ["/bin/sh", "/custom/run", "client"],
        ["/bin/sh", "/custom/launchd/run-client.sh"],
        ["/bin/device-runtime", "client"],
        ["/opt/ai-device-client/bin/ai-device-client-launcher"],
    ],
)
def test_running_client_shapes(argv):
    from device_runtime.client_macos_conflicts import is_client_command

    assert is_client_command(argv)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["/bin/echo", "device_runtime.client_cli"],
        ["/bin/echo", "/opt/ai-device-client/bin/ai-device-client-launcher"],
        ["/opt/ai-device-client/bin/ai-device-client-launcher-other"],
        ["/python", "-m", "device_runtime.cli", "center"],
    ],
)
def test_unrelated_commands(argv):
    from device_runtime.client_macos_conflicts import is_client_command

    assert not is_client_command(argv)


def test_confirmation_required_before_host_queries():
    from device_runtime.client_macos_conflicts import check_registration_conflicts

    with pytest.raises(MacClientConflict, match="confirm"):
        check_registration_conflicts(accounts=lambda: pytest.fail("host queried"))


def test_host_checks_all_account_homes_and_system_directories(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from device_runtime import client_macos_conflicts as conflicts

    calls = []
    monkeypatch.setattr(
        conflicts, "check_known_files", lambda homes, **kwargs: calls.append((homes, kwargs))
    )
    conflicts.check_registration_conflicts(
        confirm_legacy_clients_disabled=True,
        accounts=lambda: [
            SimpleNamespace(pw_dir="/Users/a"),
            SimpleNamespace(pw_dir="/var/root"),
            SimpleNamespace(pw_dir="/Users/a"),
        ],
        processes=list,
    )
    assert calls == [
        (
            {Path("/Users/a"), Path("/var/root")},
            {
                "service_directories": [
                    Path("/Library/LaunchAgents"),
                    Path("/Library/LaunchDaemons"),
                ],
                "own_plist": None,
            },
        )
    ]


@pytest.mark.parametrize("kind", ["client", "denied", "invalid", "gone"])
def test_process_discovery_errors_and_conflicts(monkeypatch, kind):
    import psutil

    from device_runtime import client_macos_conflicts as conflicts

    monkeypatch.setattr(conflicts, "check_known_files", lambda *args, **kwargs: None)

    class Process:
        def cmdline(self):
            if kind == "denied":
                raise psutil.AccessDenied(10)
            if kind == "gone":
                raise psutil.NoSuchProcess(10)
            if kind == "invalid":
                return None
            return ["/python", "-m", "device_runtime.client_cli"]

    def check():
        conflicts.check_registration_conflicts(
            confirm_legacy_clients_disabled=True, accounts=list, processes=lambda: [Process()]
        )

    if kind == "gone":
        check()
    else:
        with pytest.raises(MacClientConflict):
            check()


def test_launcher_path_argument_is_not_a_service_execution(tmp_path):
    path = tmp_path / "other.plist"
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": "custom",
                "ProgramArguments": [
                    "/bin/echo",
                    "/opt/ai-device-client/bin/ai-device-client-launcher",
                ],
            }
        )
    )
    check_known_files([], service_directories=[tmp_path])
