"""Linux delivery contracts; simulated installs never touch real instances."""

import json

import pytest
from test_installation import create, persistent
from test_installation import delivery as delivery  # noqa: PLC0414
from test_instance_lifecycle import simulated_installer as simulated_installer  # noqa: PLC0414

from device_runtime import installation as install
from device_runtime import lifecycle as life
from device_runtime.bundle import BundleError, build_bundle, inspect_bundle
from device_runtime.installation import InstallationError


@pytest.fixture
def linux_bundle(tmp_path, delivery):
    manifest = inspect_bundle(delivery)
    output = tmp_path / "linux-bundle"
    build_bundle(
        output,
        release_version="0.2.0.post5",
        wheels={c: delivery / m["path"] for c, m in manifest["components"].items()},
        constraints=delivery / "constraints.txt",
        sources={c: m["source_commit"] for c, m in manifest["components"].items()},
        target_system="linux",
    )
    return output


def test_linux_bundle_has_only_client_role(linux_bundle):
    manifest = inspect_bundle(linux_bundle)
    assert manifest["target"] == {"system": "linux", "requires_python": ">=3.13", "offline": False}
    assert set(manifest["roles"]) == {"client"}
    assert set(manifest["components"]) == {"device", "bash", "fs"}


@pytest.mark.parametrize("system", ["win32", "freebsd", "Linux", None])
def test_unknown_target_rejected_before_writes(tmp_path, system):
    output = tmp_path / "unsupported"
    with pytest.raises(BundleError, match="target"):
        build_bundle(
            output,
            release_version="0.2.0",
            wheels={},
            constraints=None,
            sources={},
            target_system=system,
        )
    assert not output.exists()


def test_linux_manifest_cannot_claim_center_role(linux_bundle):
    path = linux_bundle / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["roles"]["center"] = {"components": ["device"], "device_extras": ["server"]}
    path.write_text(json.dumps(manifest))
    with pytest.raises(BundleError, match="role layout"):
        inspect_bundle(linux_bundle)


@pytest.mark.parametrize("roles", [("center",), ("mcp",), ("client", "center")])
def test_linux_install_role_rejected_before_directory(
    tmp_path, linux_bundle, simulated_installer, monkeypatch, roles
):
    monkeypatch.setattr(install.sys, "platform", "linux")
    root = tmp_path / "wrong-role"
    with pytest.raises(InstallationError, match="roles"):
        create(root, linux_bundle, roles)
    assert not root.exists()
    assert not simulated_installer


@pytest.mark.parametrize("host,bundle_fixture", [("linux", "delivery"), ("darwin", "linux_bundle")])
def test_wrong_platform_bundle_rejected_before_directory(
    tmp_path, simulated_installer, monkeypatch, request, host, bundle_fixture
):
    bundle = request.getfixturevalue(bundle_fixture)
    monkeypatch.setattr(install.sys, "platform", host)
    root = tmp_path / "wrong-target"
    with pytest.raises(InstallationError, match="target"):
        create(root, bundle)
    assert not root.exists()
    assert not simulated_installer


def test_linux_install_rebuild_and_lifecycle(
    tmp_path, linux_bundle, simulated_installer, monkeypatch
):
    monkeypatch.setattr(install.sys, "platform", "linux")
    root = tmp_path / "instance"
    first = create(root, linux_bundle)
    assert first["roles"] == ["client"]
    before = persistent(root)
    rebuilt = install.rebuild_instance(root)
    assert rebuilt["release"] != first["release"]
    assert persistent(root) == before
    candidate = life.prepare_upgrade(root, linux_bundle, stopped=True)
    life.activate_release(root, candidate["release"], stopped=True)
    life.rollback_release(root, rebuilt["release"], stopped=True)
    backup = tmp_path / "backup"
    life.backup_instance(root, backup, stopped=True)
    restored = tmp_path / "restored"
    life.restore_instance(backup, restored)
    assert persistent(restored) == before


def test_linux_upgrade_rejects_mac_bundle_without_new_release(
    tmp_path, linux_bundle, delivery, simulated_installer, monkeypatch
):
    monkeypatch.setattr(install.sys, "platform", "linux")
    root = tmp_path / "instance"
    create(root, linux_bundle)
    pointer = (root / "active.json").read_bytes()
    releases = set((root / "releases").iterdir())
    with pytest.raises(InstallationError, match="target"):
        life.prepare_upgrade(root, delivery, stopped=True)
    assert set((root / "releases").iterdir()) == releases
    assert (root / "active.json").read_bytes() == pointer


def test_linux_backup_cannot_restore_on_mac(
    tmp_path, linux_bundle, simulated_installer, monkeypatch
):
    monkeypatch.setattr(install.sys, "platform", "linux")
    root = tmp_path / "instance"
    create(root, linux_bundle)
    backup = tmp_path / "backup"
    life.backup_instance(root, backup, stopped=True)
    monkeypatch.setattr(install.sys, "platform", "darwin")
    destination = tmp_path / "restored"
    with pytest.raises(InstallationError, match="target"):
        life.restore_instance(backup, destination)
    assert not destination.exists()


@pytest.mark.parametrize("action", ["rebuild", "activate", "rollback", "backup"])
def test_existing_linux_instance_rejects_wrong_host_without_publication(
    tmp_path, linux_bundle, simulated_installer, monkeypatch, action
):
    monkeypatch.setattr(install.sys, "platform", "linux")
    root = tmp_path / "instance"
    current = create(root, linux_bundle)
    candidate = life.prepare_upgrade(root, linux_bundle, stopped=True)
    pointer = (root / "active.json").read_bytes()
    releases = set((root / "releases").iterdir())
    before = persistent(root)
    calls = len(simulated_installer)
    monkeypatch.setattr(install.sys, "platform", "darwin")
    with pytest.raises(InstallationError, match="target"):
        if action == "rebuild":
            install.rebuild_instance(root)
        elif action == "activate":
            life.activate_release(root, candidate["release"], stopped=True)
        elif action == "rollback":
            life.rollback_release(root, current["release"], stopped=True)
        else:
            life.backup_instance(root, tmp_path / "backup", stopped=True)
    assert (root / "active.json").read_bytes() == pointer
    assert set((root / "releases").iterdir()) == releases
    assert persistent(root) == before
    assert len(simulated_installer) == calls
    assert not (tmp_path / "backup").exists()


def test_mac_backup_cannot_restore_on_linux(tmp_path, delivery, simulated_installer, monkeypatch):
    root = tmp_path / "instance"
    create(root, delivery)
    backup = tmp_path / "backup"
    life.backup_instance(root, backup, stopped=True)
    monkeypatch.setattr(install.sys, "platform", "linux")
    destination = tmp_path / "restored"
    with pytest.raises(InstallationError, match="target"):
        life.restore_instance(backup, destination)
    assert not destination.exists()


def test_linux_install_prepare_failure_never_publishes_active(
    tmp_path, linux_bundle, simulated_installer, monkeypatch
):
    monkeypatch.setattr(install.sys, "platform", "linux")

    def fail(*args, **kwargs):
        raise InstallationError("injected environment preparation failure")

    monkeypatch.setattr(install, "_verify_environment", fail)
    root = tmp_path / "instance"
    with pytest.raises(InstallationError, match="injected"):
        create(root, linux_bundle)
    assert not (root / "active.json").exists()
    assert not (root / "run").exists()


def test_linux_restore_prepare_failure_preserves_backup_and_no_active(
    tmp_path, linux_bundle, simulated_installer, monkeypatch
):
    monkeypatch.setattr(install.sys, "platform", "linux")
    root = tmp_path / "instance"
    create(root, linux_bundle)
    backup = tmp_path / "backup"
    life.backup_instance(root, backup, stopped=True)
    before = {str(p.relative_to(backup)): p.read_bytes() for p in backup.rglob("*") if p.is_file()}

    def fail(*args, **kwargs):
        raise InstallationError("injected environment preparation failure")

    monkeypatch.setattr(install, "_verify_environment", fail)
    destination = tmp_path / "restored"
    with pytest.raises(InstallationError, match="injected"):
        life.restore_instance(backup, destination)
    assert not (destination / "active.json").exists()
    assert {
        str(p.relative_to(backup)): p.read_bytes() for p in backup.rglob("*") if p.is_file()
    } == before


@pytest.fixture
def local_linux_bundle(tmp_path, linux_bundle):
    from test_dependencies import dependency_set

    dependencies = tmp_path / "local-dependencies"
    manifest = inspect_bundle(linux_bundle)
    dependency_set(dependencies, manifest["constraints"]["pins"])
    output = tmp_path / "local-linux"
    build_bundle(
        output,
        release_version="0.2.0.post6",
        wheels={c: linux_bundle / m["path"] for c, m in manifest["components"].items()},
        constraints=linux_bundle / "constraints.txt",
        sources={c: m["source_commit"] for c, m in manifest["components"].items()},
        target_system="linux",
        dependency_dir=dependencies,
    )
    return output


def test_local_dependencies_install_rebuild_upgrade(
    tmp_path, local_linux_bundle, simulated_installer, monkeypatch
):
    monkeypatch.setattr(install.sys, "platform", "linux")
    monkeypatch.setattr(install.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(install.platform, "libc_ver", lambda: ("glibc", "2.17"))
    root = tmp_path / "local-instance"
    create(root, local_linux_bundle)
    install.rebuild_instance(root)
    life.prepare_upgrade(root, local_linux_bundle, stopped=True)
    installs = [
        args for args, _ in simulated_installer if args[1:4] == ["--no-config", "pip", "install"]
    ]
    assert len(installs) == 3
    for args in installs:
        assert "--no-index" in args
        assert args[args.index("--only-binary") + 1] == ":all:"
        dependency_wheels = [a for a in args if "/dependencies/" in a]
        assert len(dependency_wheels) == 1
        assert all(str(root / "releases") in a for a in dependency_wheels)
    active = json.loads((root / "active.json").read_text())
    snapshot = root / "releases" / active["release"] / "bundle"
    wheel = next((snapshot / "dependencies").glob("*.whl"))
    wheel.write_bytes(b"tampered")
    before = len(simulated_installer)
    with pytest.raises((InstallationError, BundleError)):
        install.rebuild_instance(root)
    assert len(simulated_installer) == before


@pytest.mark.parametrize(
    "machine,libc",
    [("aarch64", ("glibc", "2.17")), ("x86_64", ("musl", "1.2")), ("x86_64", ("glibc", "2.16"))],
)
def test_local_dependency_wrong_host_rejected(
    tmp_path, local_linux_bundle, simulated_installer, monkeypatch, machine, libc
):
    monkeypatch.setattr(install.sys, "platform", "linux")
    monkeypatch.setattr(install.platform, "machine", lambda: machine)
    monkeypatch.setattr(install.platform, "libc_ver", lambda: libc)
    root = tmp_path / "wrong-host"
    with pytest.raises(InstallationError, match="glibc"):
        create(root, local_linux_bundle)
    assert not root.exists()
    assert not simulated_installer


def test_native_dependency_directory_lifecycle(
    tmp_path, linux_bundle, simulated_installer, monkeypatch
):
    from test_dependencies import dependency_set

    monkeypatch.setattr(install.sys, "platform", "linux")
    monkeypatch.setattr(install.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(install.platform, "libc_ver", lambda: ("glibc", "2.17"))
    manifest = inspect_bundle(linux_bundle)
    dependencies = tmp_path / "native-wheels"
    dependency_set(dependencies, manifest["constraints"]["pins"])
    root = tmp_path / "native-instance"
    install.install_instance(
        linux_bundle,
        instance_id="native",
        roles=["client"],
        root=root,
        server_url="https://example.invalid",
        dependency_dir=dependencies,
    )
    install.rebuild_instance(root, bundle=linux_bundle, dependency_dir=dependencies)
    life.prepare_upgrade(root, linux_bundle, stopped=True, dependency_dir=dependencies)
    assert len([a for a, _ in simulated_installer if "--no-index" in a]) == 3
    # Existing releases remain usable even when the original local directory disappears.
    for path in dependencies.iterdir():
        path.unlink()
    install.rebuild_instance(root)
    assert len([a for a, _ in simulated_installer if "--no-index" in a]) == 4
    assert inspect_bundle(linux_bundle)["format_version"] == 2
