"""Linux delivery contracts; simulated installs never touch real instances."""

import json

import pytest
from test_installation import create
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
    with pytest.raises(BundleError, match="target|complete client DEB"):
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
    with pytest.raises(InstallationError, match="roles|complete client DEB"):
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
    with pytest.raises(InstallationError, match="target|complete client DEB"):
        create(root, bundle)
    assert not root.exists()
    assert not simulated_installer


def test_mac_backup_cannot_restore_on_linux(tmp_path, delivery, simulated_installer, monkeypatch):
    root = tmp_path / "instance"
    create(root, delivery)
    backup = tmp_path / "backup"
    life.backup_instance(root, backup, stopped=True)
    monkeypatch.setattr(install.sys, "platform", "linux")
    destination = tmp_path / "restored"
    with pytest.raises(InstallationError, match="target|complete client DEB"):
        life.restore_instance(backup, destination)
    assert not destination.exists()


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


@pytest.mark.parametrize(
    "machine,libc",
    [("aarch64", ("glibc", "2.27")), ("x86_64", ("musl", "1.2")), ("x86_64", ("glibc", "2.16"))],
)
def test_local_dependency_wrong_host_rejected(
    tmp_path, local_linux_bundle, simulated_installer, monkeypatch, machine, libc
):
    monkeypatch.setattr(install.sys, "platform", "linux")
    monkeypatch.setattr(install.platform, "machine", lambda: machine)
    monkeypatch.setattr(install.platform, "libc_ver", lambda: libc)
    root = tmp_path / "wrong-host"
    with pytest.raises(InstallationError, match="complete client DEB"):
        create(root, local_linux_bundle)
    assert not root.exists()
    assert not simulated_installer


@pytest.mark.parametrize(
    "required,host,accepted",
    [
        ("2.17", "2.17", True),
        ("2.17", "2.16", False),
        ("2.27", "2.17", False),
        ("2.27", "2.26", False),
        ("2.27", "2.27", True),
        ("2.27", "2.39", True),
    ],
)
def test_host_requirement_uses_immutable_manifest(monkeypatch, required, host, accepted):
    from device_runtime.dependencies import TARGET

    monkeypatch.setattr(install.sys, "platform", "linux")
    monkeypatch.setattr(install.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(install.platform, "libc_ver", lambda: ("glibc", host))
    manifest = {
        "format_version": 3,
        "target": {"system": "linux"},
        "roles": ["mcp"],
        "dependencies": {"manifest": {"target": {**TARGET, "glibc": required}}},
    }
    if accepted:
        install._check_target(manifest, ["mcp"])
    else:
        with pytest.raises(InstallationError, match="glibc"):
            install._check_target(manifest, ["mcp"])
