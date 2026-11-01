"""LaunchAgent deployment: file shapes and refusals, no real launchctl calls."""

import plistlib
from pathlib import Path

import pytest

from device_runtime import launchd_deploy as deploy


@pytest.fixture
def instance(tmp_path, monkeypatch):
    root = tmp_path / "inst"
    (root / "config").mkdir(parents=True)
    (root / "config" / "roles.json").write_text('{"client": {}}')
    (root / "run").write_text("#!/bin/sh\n")
    (root / "run").chmod(0o755)
    monkeypatch.setattr(deploy.sys, "platform", "darwin")
    return root


def test_label_rejects_unsafe_instance_names():
    assert deploy.label_for("macmini") == "com.device-runtime.macmini"
    for bad in ("", "-x", "a b", "a/b", "../x"):
        with pytest.raises(deploy.LaunchdError):
            deploy.label_for(bad)


def test_job_passes_program_through_wrapper(instance):
    job = deploy.build_job(
        instance,
        label="com.device-runtime.inst",
        home=Path("/Users/x"),
        path_env="/usr/bin:/bin",
    )
    # The token must never reach the plist: ~/Library/LaunchAgents is readable.
    assert job["ProgramArguments"] == ["/bin/sh", str(instance / "launchd" / "start.sh")]
    assert "DEVICE_RUNTIME_TOKEN" not in repr(job)
    assert job["KeepAlive"] is True and job["RunAtLoad"] is True


def test_dry_run_writes_nothing(instance, monkeypatch):
    monkeypatch.setattr(deploy, "_run", lambda *a, **k: pytest.fail("must not call launchctl"))
    result = deploy.install(instance, dry_run=True)
    assert result["status"] == "dry-run"
    assert not (instance / "launchd" / "start.sh").exists()
    assert not (instance / "launchd" / "com.device-runtime.inst.plist").exists()


def test_install_refuses_without_env_file(instance, monkeypatch):
    monkeypatch.setattr(deploy, "_run", lambda *a, **k: pytest.fail("must not call launchctl"))
    with pytest.raises(deploy.LaunchdError, match="env file"):
        deploy.install(instance)


def test_install_refuses_without_run_entry(instance, monkeypatch):
    (instance / "run").unlink()
    with pytest.raises(deploy.LaunchdError, match="stable run entry"):
        deploy.install(instance, dry_run=True)


def test_refuses_on_non_darwin(instance, monkeypatch):
    monkeypatch.setattr(deploy.sys, "platform", "linux")
    with pytest.raises(deploy.LaunchdError, match="requires macOS"):
        deploy.install(instance, dry_run=True)


def test_install_boots_out_before_bootstrap(instance, monkeypatch):
    calls = []
    monkeypatch.setattr(
        deploy, "_run", lambda argv, check=True: calls.append((argv, check)) or None
    )
    (instance / "launchd").mkdir()
    (instance / "launchd" / "client.env").write_text("DEVICE_RUNTIME_TOKEN=x\n")
    monkeypatch.setattr(deploy, "_installed_path", lambda label: instance / "LaunchAgents.plist")
    result = deploy.install(instance)
    assert result["status"] == "installed"
    verbs = [argv[1] for argv, _ in calls]
    # A loaded job keeps the old definition; bootout must precede bootstrap.
    assert verbs == ["bootout", "bootstrap"]
    assert calls[0][1] is False  # bootout tolerates "not loaded"


def test_wrapper_handles_both_entry_shapes(instance, monkeypatch):
    monkeypatch.setattr(deploy, "_run", lambda argv, check=True: None)
    (instance / "launchd").mkdir()
    (instance / "launchd" / "client.env").write_text("DEVICE_RUNTIME_TOKEN=x\n")
    monkeypatch.setattr(deploy, "_installed_path", lambda label: instance / "LaunchAgents.plist")
    deploy.install(instance)
    wrapper = (instance / "launchd" / "start.sh").read_text()
    # Launcher form takes the role only; legacy form needs the release first.
    assert wrapper.startswith("#!/bin/sh")
    assert 'exec "$root/run" client' in wrapper
    assert 'exec "$root/run" "$release" client' in wrapper
    assert "umask 077" in wrapper


def test_plist_is_written_without_private_keys(instance, monkeypatch):
    monkeypatch.setattr(deploy, "_run", lambda argv, check=True: None)
    (instance / "launchd").mkdir()
    (instance / "launchd" / "client.env").write_text("DEVICE_RUNTIME_TOKEN=x\n")
    target = instance / "LaunchAgents.plist"
    monkeypatch.setattr(deploy, "_installed_path", lambda label: target)
    deploy.install(instance)
    job = plistlib.loads(target.read_bytes())
    assert not [k for k in job if k.startswith("__")]
    assert job["Label"] == "com.device-runtime.inst"


def test_profile_written_without_upgrade_permission(instance, monkeypatch):
    result = deploy.write_profile(instance)
    assert result["allow_upgrade"] is False
    import json

    value = json.loads((instance / "upgrade-profile.json").read_text())
    assert value["label"] == "com.device-runtime.inst"
    assert value["allow_upgrade"] is False
    assert (instance / "upgrade-profile.json").stat().st_mode & 0o077 == 0


def test_profile_can_grant_upgrade_explicitly(instance):
    result = deploy.write_profile(instance, allow_upgrade=True)
    assert result["allow_upgrade"] is True


def test_uninstall_removes_both_copies(instance, monkeypatch):
    monkeypatch.setattr(deploy, "_run", lambda argv, check=True: None)
    local = instance / "launchd" / "com.device-runtime.inst.plist"
    local.parent.mkdir()
    local.write_text("x")
    installed = instance / "LaunchAgents.plist"
    installed.write_text("x")
    monkeypatch.setattr(deploy, "_installed_path", lambda label: installed)
    deploy.uninstall(instance)
    assert not local.exists() and not installed.exists()
