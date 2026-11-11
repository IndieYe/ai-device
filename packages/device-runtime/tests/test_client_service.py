from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from device_runtime import client_service


def reply(active="inactive", sub="dead", pid=0, control=0):
    return SimpleNamespace(
        returncode=0,
        stdout=(
            f"LoadState=loaded\nActiveState={active}\nSubState={sub}\nMainPID={pid}\nControlPID={control}\n"
        ),
    )


@pytest.mark.parametrize(
    "value",
    [
        reply("activating", "start", 1),
        reply("inactive", pid=1),
        reply("failed", control=5),
        reply("active", "running", 0),
    ],
)
def test_unknown_state_refuses(value):
    service = client_service.SystemdClient(run=lambda *a, **k: value)
    with pytest.raises(client_service.ServiceError):
        service.state()


def test_stop_holds_lease_until_confirmation(monkeypatch):
    held = []
    events = []
    responses = iter([reply("active", "running", 42), reply(), reply()])

    @contextmanager
    def lease(path):
        held.append(True)
        try:
            yield
        finally:
            held.clear()

    def run(argv, **kwargs):
        events.append(argv[1])
        if len(events) > 1:
            assert held
        if "--property=ControlGroup" in argv:
            return SimpleNamespace(returncode=0, stdout="")
        return next(responses)

    monkeypatch.setattr(client_service, "idle_lease", lease)
    client_service.SystemdClient(run=run).stop_for_install("socket")
    assert events == ["show", "stop", "show", "show"]
    assert not held


def test_stopped_does_not_require_socket(monkeypatch):
    monkeypatch.setattr(client_service, "idle_lease", lambda _: pytest.fail("unexpected lease"))
    client_service.SystemdClient(
        run=lambda argv, **k: (
            SimpleNamespace(returncode=0, stdout="")
            if "--property=ControlGroup" in argv
            else reply()
        )
    ).stop_for_install("socket")


@pytest.mark.parametrize(
    "user", ["", None, "a\nExecStart=/bin/false", "name with space", "../root"]
)
def test_unit_rejects_ambiguous_user(user):
    with pytest.raises(ValueError):
        client_service.render_unit(user)


def test_unit_uses_explicit_user_and_never_forces_stop():
    unit = client_service.render_unit("device")
    assert "User=device\n" in unit
    assert "SendSIGKILL=no\n" in unit
    assert "TimeoutStopSec=30\n" in unit
    assert " -I -m device_runtime.client_cli --config " in unit
    assert "StateDirectoryMode=0700" in unit
    assert "StandardError=append:/var/log/ai-device-client/client.log" in unit
    assert "DEVICE_RUNTIME_TOKEN" not in unit


def test_stop_failure_releases_lease(monkeypatch):
    held = []

    @contextmanager
    def lease(path):
        held.append(True)
        try:
            yield
        finally:
            held.clear()

    def run(argv, **kwargs):
        if argv[1] == "show":
            return reply("active", "running", 42)
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(client_service, "idle_lease", lease)
    with pytest.raises(client_service.ServiceError):
        client_service.SystemdClient(run=run).stop_for_install("socket")
    assert not held


def test_explicit_start_clears_prior_start_limit():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[1])
        if "--property=FragmentPath" in argv:
            return SimpleNamespace(
                returncode=0, stdout="/etc/systemd/system/ai-device-client.service\n"
            )
        if "--property=DropInPaths" in argv:
            return SimpleNamespace(returncode=0, stdout="")
        if "--property=SendSIGKILL" in argv:
            return SimpleNamespace(returncode=0, stdout="no")
        if "--property=KillMode" in argv:
            return SimpleNamespace(returncode=0, stdout="control-group")
        return reply("active", "running", 42)

    client_service.SystemdClient(run=run).start()
    assert calls == [
        "daemon-reload",
        "show",
        "show",
        "show",
        "show",
        "reset-failed",
        "start",
        "show",
    ]


@pytest.mark.parametrize(
    "fragment,overrides",
    [
        ("/usr/lib/systemd/system/ai-device-client.service", ""),
        (
            "/etc/systemd/system/ai-device-client.service",
            "/etc/systemd/system/ai-device-client.service.d/custom.conf",
        ),
    ],
)
def test_unexpected_registration_is_rejected(fragment, overrides):
    def run(argv, **kwargs):
        return SimpleNamespace(
            returncode=0, stdout=fragment if "--property=FragmentPath" in argv else overrides
        )

    with pytest.raises(client_service.ServiceError):
        client_service.SystemdClient(run=run).validate_registration()


def test_configuration_probe_runs_as_service_user():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="device\n" if argv[0] == "systemctl" else "")

    client_service.SystemdClient(run=run).validate_config()
    assert calls[1][:4] == ["runuser", "-u", "device", "--"]


@pytest.mark.parametrize("outcome", ["timeout", "still-running"])
def test_stop_timeout_or_unconfirmed_stop_releases_lease(monkeypatch, outcome):
    import subprocess

    held = []
    calls = []

    @contextmanager
    def lease(path):
        held.append(True)
        try:
            yield
        finally:
            held.clear()

    def run(argv, **kwargs):
        calls.append(argv[1])
        if argv[1] == "stop":
            assert held
            if outcome == "timeout":
                raise subprocess.TimeoutExpired(argv, 45)
            return SimpleNamespace(returncode=0, stdout="")
        return reply("active", "running", 42)

    monkeypatch.setattr(client_service, "idle_lease", lease)
    with pytest.raises((subprocess.TimeoutExpired, client_service.ServiceError)):
        client_service.SystemdClient(run=run).stop_for_install("socket")
    assert not held
    assert calls == (["show", "stop"] if outcome == "timeout" else ["show", "stop", "show"])


def test_configuration_probe_enforces_installed_state_path():
    def run(argv, **kwargs):
        if argv[0] == "systemctl":
            return SimpleNamespace(returncode=0, stdout="device\n")
        assert "assert str(a.state_dir)=='/var/lib/ai-device-client'" in argv[-1]
        return SimpleNamespace(returncode=1, stdout="")

    with pytest.raises(client_service.ServiceError, match="configuration"):
        client_service.SystemdClient(run=run).validate_config()


@pytest.mark.parametrize(
    "sigkill,killmode", [("yes", "control-group"), ("no", "process"), ("", "")]
)
def test_unsafe_shutdown_policy_is_rejected(sigkill, killmode):
    values = {
        "FragmentPath": "/etc/systemd/system/ai-device-client.service",
        "DropInPaths": "",
        "SendSIGKILL": sigkill,
        "KillMode": killmode,
    }

    def run(argv, **kwargs):
        key = next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--property="))
        return SimpleNamespace(returncode=0, stdout=values[key])

    with pytest.raises(client_service.ServiceError):
        client_service.SystemdClient(run=run).validate_registration()


@pytest.mark.parametrize("populated", ["1", "0", None])
def test_stopped_state_checks_descendant_cgroup(tmp_path, monkeypatch, populated):
    monkeypatch.setattr(client_service, "CGROUP_ROOT", tmp_path)
    # The unified hierarchy is recognised by this file, not by the mount point.
    (tmp_path / "cgroup.controllers").write_text("cpuset cpu io memory\n")
    directory = tmp_path / "system.slice/client.service"
    directory.mkdir(parents=True)
    if populated is not None:
        (directory / "cgroup.events").write_text(f"populated {populated}\nfrozen 0\n")

    def run(argv, **kwargs):
        if "--property=ControlGroup" in argv:
            return SimpleNamespace(returncode=0, stdout="/system.slice/client.service")
        return reply("failed")

    service = client_service.SystemdClient(run=run)
    if populated == "0":
        assert service.state() == "stopped"
    else:
        with pytest.raises(client_service.ServiceError):
            service.state()


def _v1_layout(root):
    """Build a legacy hierarchy the way a v1 host actually lays it out."""
    (root / "systemd").mkdir(parents=True)
    # v1 has no cgroup.controllers anywhere.
    return root / "systemd/system.slice/client.service"


def test_cgroup_version_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(client_service, "CGROUP_ROOT", tmp_path)
    assert client_service.cgroup_v2() is None  # neither layout present
    (tmp_path / "cgroup.controllers").write_text("cpu\n")
    assert client_service.cgroup_v2() is True
    (tmp_path / "cgroup.controllers").unlink()
    (tmp_path / "systemd").mkdir()
    assert client_service.cgroup_v2() is False


@pytest.mark.parametrize("procs", ["", "\n", "1234\n", "1234\n5678\n"])
def test_v1_populated_uses_cgroup_procs(tmp_path, procs):
    """A v1 group is empty only when cgroup.procs holds no process."""
    directory = tmp_path / "unit"
    directory.mkdir()
    (directory / "cgroup.procs").write_text(procs)
    assert client_service.cgroup_group_populated(directory, False) is bool(procs.strip())


def test_v1_populated_is_unknown_when_procs_missing(tmp_path):
    """Absent file is 'cannot tell', never 'empty'."""
    directory = tmp_path / "unit"
    directory.mkdir()
    assert client_service.cgroup_group_populated(directory, False) is None


def test_v1_unit_directory_resolves_under_systemd_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(client_service, "CGROUP_ROOT", tmp_path)
    (tmp_path / "systemd").mkdir()
    resolved = client_service.cgroup_unit_directory("/system.slice/client.service")
    assert resolved == tmp_path / "systemd/system.slice/client.service"


def test_stopped_state_refuses_when_v1_group_has_processes(tmp_path, monkeypatch):
    """The check must actually run on v1, where it previously passed silently."""
    monkeypatch.setattr(client_service, "CGROUP_ROOT", tmp_path)
    directory = _v1_layout(tmp_path)
    directory.mkdir(parents=True)
    (directory / "cgroup.procs").write_text("4242\n")

    def run(argv, **kwargs):
        if "--property=ControlGroup" in argv:
            return SimpleNamespace(returncode=0, stdout="/system.slice/client.service")
        return reply("failed")

    with pytest.raises(client_service.ServiceError, match="live processes"):
        client_service.SystemdClient(run=run).state()


def test_stopped_state_allows_empty_v1_group(tmp_path, monkeypatch):
    monkeypatch.setattr(client_service, "CGROUP_ROOT", tmp_path)
    directory = _v1_layout(tmp_path)
    directory.mkdir(parents=True)
    (directory / "cgroup.procs").write_text("")

    def run(argv, **kwargs):
        if "--property=ControlGroup" in argv:
            return SimpleNamespace(returncode=0, stdout="/system.slice/client.service")
        return reply("failed")

    assert client_service.SystemdClient(run=run).state() == "stopped"


def test_absent_group_directory_warns_instead_of_silently_passing(tmp_path, monkeypatch, capsys):
    """This is the branch that made the protection vanish on v1.

    The old code returned without a word whenever /sys/fs/cgroup<group> was
    missing, which is what always happens under the legacy hierarchy. The
    behaviour stays permissive for hosts without a matching mount, but it must
    no longer be silent.
    """
    monkeypatch.setattr(client_service, "CGROUP_ROOT", tmp_path)
    (tmp_path / "systemd").mkdir()

    def run(argv, **kwargs):
        if "--property=ControlGroup" in argv:
            return SimpleNamespace(returncode=0, stdout="/system.slice/client.service")
        return reply("failed")

    assert client_service.SystemdClient(run=run).state() == "stopped"
    assert "nothing to verify" in capsys.readouterr().err


def test_unrecognisable_hierarchy_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(client_service, "CGROUP_ROOT", tmp_path)

    def run(argv, **kwargs):
        if "--property=ControlGroup" in argv:
            return SimpleNamespace(returncode=0, stdout="/system.slice/client.service")
        return reply("failed")

    assert client_service.SystemdClient(run=run).state() == "stopped"
    assert "cgroup hierarchy" in capsys.readouterr().err

