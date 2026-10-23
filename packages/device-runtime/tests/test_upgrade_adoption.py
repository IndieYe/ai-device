"""Review-only generated units: no real systemctl, users or host directory changes."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def renderer():
    path = Path(__file__).resolve().parents[3] / "scripts/render_upgrade_adoption.py"
    spec = importlib.util.spec_from_file_location("render_upgrade_adoption", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration():
    return {
        "root": "/var/lib/device-example",
        "client_unit": "device-example.service",
        "client_user": "device-example",
        "client_group": "device-example",
        "token_file": "/etc/device-example/client.env",
        "supervisor_home": "/opt/device-upgrader/approved",
        "supervisor_python": "/opt/device-upgrader/approved/venv/bin/python",
        "upgrade_id": "approved-one",
        "channel": "stable",
        "allowed_versions": ["0.2.2"],
        "bundle_channels": {"a" * 64: "stable"},
        "maintenance_windows_utc": [
            {"start": "2030-01-01T00:00:00Z", "end": "2030-01-01T01:00:00Z"}
        ],
    }


def test_render_disabled_private_and_independent(renderer, tmp_path):
    output = renderer.render(configuration(), tmp_path / "review")
    assert output.stat().st_mode & 0o777 == 0o700
    profile = json.loads((output / "upgrade-profile.json").read_text())
    assert profile["allow_upgrade"] is False
    client = (output / "client.service").read_text()
    supervisor = (output / "supervisor.service").read_text()
    assert "ExecStart=/var/lib/device-example/run client\n" in client
    assert "EnvironmentFile=/etc/device-example/client.env\n" in client
    assert "Restart=no\n" in supervisor and "[Install]" not in supervisor
    assert "\nPartOf=" not in supervisor and "\nBindsTo=" not in supervisor
    assert "--upgrade-id approved-one --confirm-service-change" in supervisor
    assert "-I -B -m device_runtime.cli" in supervisor
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
    with pytest.raises(FileExistsError):
        renderer.render(configuration(), output)


@pytest.mark.parametrize(
    "field,value",
    [
        ("root", "/tmp/root\nExecStart=/bin/false"),
        ("root", "/tmp/%i"),
        ("root", "/tmp/../root"),
        ("root", "/"),
        ("client_user", "root"),
        ("client_unit", "bad@.service"),
        ("upgrade_id", "bad value"),
        ("supervisor_home", "/var/lib/device-example/upgrader"),
        ("supervisor_python", "/unrelated/python"),
        ("token_file", "/var/lib/device-example/token.env"),
    ],
)
def test_render_rejects_ambiguous_bindings(renderer, tmp_path, field, value):
    config = configuration()
    config[field] = value
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        renderer.render(config, output)
    assert not output.exists()
