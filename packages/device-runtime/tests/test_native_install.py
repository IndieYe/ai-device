import os
import shutil
from types import SimpleNamespace

import pytest

from device_runtime import native_install as native
from device_runtime.installation import InstallationError


@pytest.fixture
def payload(tmp_path, monkeypatch):
    root = tmp_path / "payload"
    (root / "runtime/bin").mkdir(parents=True)
    python = root / "runtime/bin/python3.13"
    python.write_text("fixture")
    python.chmod(0o755)
    (root / "tools").mkdir()
    uv = root / "tools/uv"
    uv.write_text("fixture")
    uv.chmod(0o755)
    bundle = root / "bundle"
    (bundle / "wheels").mkdir(parents=True)
    (bundle / "dependencies").mkdir()
    for name in ("manifest.json", "constraints.txt", "wheels/device.whl",
                 "dependencies/manifest.json", "dependencies/dep.whl"):
        (bundle / name).write_bytes(b"1234567890")
    monkeypatch.setattr(native.sys, "executable", str(python))
    monkeypatch.setattr(
        native,
        "inspect_bundle",
        lambda _: {
            "format_version": 3,
            "target": {"system": "linux"},
            "components": {"device": {"path": "wheels/device.whl"}},
            "dependencies": {"manifest": {"wheels": [{"path": "dep.whl"}]}},
        },
    )
    return root


@pytest.mark.parametrize("free", [0, 49])
def test_native_install_rejects_insufficient_bundle_copy_space_before_creation(
    tmp_path, monkeypatch, payload, free
):
    destination = tmp_path / "not-yet-created" / "instance"
    checked = []
    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: checked.append(path) or SimpleNamespace(free=free)
    )
    monkeypatch.setattr(native, "install_instance", lambda *a, **k: pytest.fail("core called"))
    with pytest.raises(InstallationError, match="insufficient space for bundle copy"):
        native.install_payload(
            payload, root=destination, instance_id="fixture", server_url="https://example.invalid"
        )
    assert checked == [tmp_path]
    assert not destination.parent.exists()


def test_native_install_rejects_unavailable_disk_space_without_creating_root(tmp_path, monkeypatch, payload):
    destination = tmp_path / "instance"

    def fail(_path):
        raise OSError("disk usage unavailable")

    monkeypatch.setattr(shutil, "disk_usage", fail)
    monkeypatch.setattr(native, "install_instance", lambda *a, **k: pytest.fail("core called"))
    with pytest.raises(InstallationError, match="cannot determine available space"):
        native.install_payload(
            payload, root=destination, instance_id="fixture", server_url="https://example.invalid"
        )
    assert not destination.exists()


def test_native_install_space_lower_bound_allows_equal_space(tmp_path, monkeypatch, payload):
    # The bundle is 50 bytes and the private runtime copy adds the 7-byte
    # interpreter, so an exact lower bound of 57 must still be accepted.
    monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=57))
    monkeypatch.setattr(native, "install_instance", lambda *a, **k: {"release": "fixture"})
    result = native.install_payload(
        payload, root=tmp_path / "instance", instance_id="fixture", server_url="https://example.invalid"
    )
    assert result["release"] == "fixture"


@pytest.mark.parametrize("case", ["missing-file", "dangling-parent", "file-parent"])
def test_native_install_rejects_unverifiable_space_inputs(tmp_path, monkeypatch, payload, case):
    parent = tmp_path / "parent"
    if case == "missing-file":
        (payload / "bundle/dependencies/dep.whl").unlink()
        parent = tmp_path
    elif case == "dangling-parent":
        parent.symlink_to(tmp_path / "missing")
    else:
        parent.write_text("not a directory")
    monkeypatch.setattr(native, "install_instance", lambda *a, **k: pytest.fail("core called"))
    with pytest.raises(InstallationError, match="cannot determine available space"):
        native.install_payload(
            payload, root=parent / "instance", instance_id="fixture", server_url="https://example.invalid"
        )
    if case == "file-parent":
        assert parent.read_text() == "not a directory"
    if case == "dangling-parent":
        assert parent.is_symlink()


def test_native_install_calls_existing_core_and_restores_path(tmp_path, monkeypatch, payload):
    previous = os.environ.get("PATH")
    calls = []

    def install(bundle, **kwargs):
        calls.append((bundle, kwargs))
        assert os.environ["PATH"].startswith(str(payload / "tools"))
        return {"release": "fixture"}

    monkeypatch.setattr(native, "install_instance", install)
    result = native.install_payload(
        payload,
        root=tmp_path / "instance",
        instance_id="fixture",
        server_url="https://example.invalid",
    )
    assert result["paired"] is False and result["service_started"] is False
    assert calls[0][1]["roles"] == ["client"]
    assert calls[0][1]["runner_python"] == payload / "runtime/bin/python3.13"
    assert os.environ.get("PATH") == previous


def test_native_install_restores_path_on_failure(tmp_path, monkeypatch, payload):
    monkeypatch.delenv("PATH", raising=False)

    def fail(*a, **kw):
        raise InstallationError("fixture failure")

    monkeypatch.setattr(native, "install_instance", fail)
    with pytest.raises(InstallationError, match="fixture failure"):
        native.install_payload(
            payload,
            root=tmp_path / "instance",
            instance_id="fixture",
            server_url="https://example.invalid",
        )
    assert "PATH" not in os.environ


@pytest.mark.parametrize("case", ["existing", "missing-tool", "wrong-python", "online"])
def test_native_preflight_no_mutation(tmp_path, monkeypatch, payload, case):
    destination = tmp_path / "instance"
    if case == "existing":
        destination.mkdir()
        (destination / "preserve").write_text("keep")
    elif case == "missing-tool":
        (payload / "tools/uv").unlink()
    elif case == "wrong-python":
        monkeypatch.setattr(native.sys, "executable", "/nonexistent/python")
    else:
        monkeypatch.setattr(
            native,
            "inspect_bundle",
            lambda _: {
                "format_version": 2,
                "target": {"system": "linux"},
            },
        )
    monkeypatch.setattr(native, "install_instance", lambda *a, **k: pytest.fail("core called"))
    with pytest.raises(InstallationError):
        native.install_payload(
            payload, root=destination, instance_id="fixture", server_url="https://example.invalid"
        )
    if case == "existing":
        assert (destination / "preserve").read_text() == "keep"
    else:
        assert not destination.exists()


def test_native_client_rejects_root(tmp_path, monkeypatch):
    monkeypatch.setattr(native.os, "geteuid", lambda: 0)
    with pytest.raises(InstallationError, match="non-root"):
        native.install_payload(
            tmp_path / "payload",
            root=tmp_path / "instance",
            instance_id="fixture",
            server_url="https://example.invalid",
        )
    assert not (tmp_path / "instance").exists()


def test_runner_must_be_absolute_without_private_runtime(tmp_path, monkeypatch):
    """Without a private runtime the caller's interpreter must already exist."""
    with pytest.raises(InstallationError, match="executable absolute shebang path"):
        native.install_instance(
            tmp_path / "bundle",
            instance_id="fixture",
            roles=["client"],
            root=tmp_path / "instance",
            server_url="https://example.invalid",
            runner_python=tmp_path / "missing-python",
        )


def test_runtime_source_must_be_a_plain_directory(tmp_path):
    """A symlinked runtime would reintroduce the dependency we are removing."""
    target = tmp_path / "real-runtime"
    target.mkdir()
    link = tmp_path / "runtime-link"
    link.symlink_to(target)
    with pytest.raises(InstallationError, match="plain directory"):
        native.install_instance(
            tmp_path / "bundle",
            instance_id="fixture",
            roles=["client"],
            root=tmp_path / "instance",
            server_url="https://example.invalid",
            runtime_source=link,
        )
