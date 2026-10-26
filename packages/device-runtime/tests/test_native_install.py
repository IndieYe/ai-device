import os

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
    monkeypatch.setattr(native.sys, "executable", str(python))
    monkeypatch.setattr(
        native,
        "inspect_bundle",
        lambda _: {
            "format_version": 3,
            "target": {"system": "linux"},
        },
    )
    return root


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
