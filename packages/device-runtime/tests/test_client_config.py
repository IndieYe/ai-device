import json
from pathlib import Path

import pytest

from device_runtime.client_config import load


@pytest.fixture
def config(tmp_path):
    token = tmp_path / "token"
    token.write_text("test-only-client-secret\n")
    token.chmod(0o600)
    path = tmp_path / "client.json"
    value = {
        "server_url": "wss://example.test",
        "device_id": "client-one",
        "state_dir": str(tmp_path / "state"),
        "token_file": str(token),
    }
    path.write_text(json.dumps(value))
    return path, value


def test_load_does_not_create_or_modify_state(config):
    path, _value = config
    original = path.read_bytes()
    args, token = load(path)
    assert args.device_id == "client-one"
    assert args.maintenance is True
    assert token == "test-only-client-secret"
    assert not args.state_dir.exists()
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("state_dir", "relative"),
        ("token_file", "relative"),
        ("max_active_tasks", True),
        ("max_concurrent_requests", 0),
        ("device_id", "invalid/id"),
        ("server_url", "http://example.test"),
        ("unexpected", "not-allowed"),
    ],
)
def test_reject_invalid_configuration(config, field, value):
    path, content = config
    content[field] = value
    path.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="configuration or private token"):
        load(path)


def test_token_permissions(config):
    path, content = config
    from pathlib import Path

    Path(content["token_file"]).chmod(0o644)
    with pytest.raises(ValueError, match="configuration or private token"):
        load(path)


def test_config_entry_passes_token_without_environment(config, monkeypatch):
    from device_runtime import client_cli

    path, content = config
    observed = []
    monkeypatch.delenv("DEVICE_RUNTIME_TOKEN", raising=False)
    monkeypatch.setattr(client_cli, "run", lambda args, *, token: observed.append((args, token)))
    client_cli.main(["--config", str(path)])
    assert observed[0][0].device_id == content["device_id"]
    assert observed[0][1] == "test-only-client-secret"


def test_initialize_is_private_and_never_overwrites(tmp_path):
    import stat

    from device_runtime.client_config import initialize

    directory = tmp_path / "config"
    arguments = {
        "server_url": "wss://example.test",
        "device_id": "client-one",
        "state_dir": tmp_path / "state",
        "token": "test-only-client-secret",
    }
    path = initialize(directory, **arguments)
    original = path.read_bytes()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((directory / "token").stat().st_mode) == 0o600
    assert load(path)[1] == arguments["token"]
    with pytest.raises(FileExistsError):
        initialize(directory, **arguments)
    assert path.read_bytes() == original
    assert not (tmp_path / "state").exists()


def test_initialize_invalid_input_leaves_no_directory(tmp_path):
    from device_runtime.client_config import initialize

    with pytest.raises(ValueError):
        initialize(
            tmp_path / "config",
            server_url="wss://example.test",
            device_id="client-one",
            state_dir=tmp_path / "state",
            token="short",
        )
    assert not (tmp_path / "config").exists()


def test_setup_cli_token_stdin_and_no_overwrite(tmp_path):
    import subprocess
    import sys

    command = [
        sys.executable,
        "-m",
        "device_runtime.client_setup",
        "--config-dir",
        str(tmp_path / "config"),
        "--state-dir",
        str(tmp_path / "state"),
        "--server-url",
        "wss://example.test",
        "--device-id",
        "test-one",
        "--token-stdin",
    ]
    token = "test-only-secret-not-for-output"
    result = subprocess.run(
        command, input=token, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
    assert token not in result.stdout + result.stderr
    again = subprocess.run(
        command, input=token, text=True, capture_output=True, timeout=10, check=False
    )
    assert again.returncode == 2
    assert "not overwritten" in again.stderr
    assert token not in again.stdout + again.stderr


@pytest.mark.parametrize("target", ["config", "token"])
def test_fifo_is_rejected_without_waiting_for_writer(config, target):
    import os
    import subprocess
    import sys

    path, content = config
    fifo = path if target == "config" else Path(content["token_file"])
    fifo.unlink()
    os.mkfifo(fifo, 0o600)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from device_runtime.client_config import load; "
                "\ntry: load(sys.argv[1])\nexcept ValueError: sys.exit(0)\n"
                "sys.exit(9)"
            ),
            str(path),
        ],
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_oversized_config_is_rejected(config):
    path, _ = config
    path.write_text(" " * 65537)
    with pytest.raises(ValueError, match="configuration"):
        load(path)


@pytest.mark.parametrize("field", ["device_id", "token"])
def test_cli_errors_do_not_expose_input_or_traceback(config, field):
    import subprocess
    import sys

    path, content = config
    sensitive = "PRIVATE_TEST_MARKER_DO_NOT_LOG"
    if field == "device_id":
        content["device_id"] = sensitive + " invalid identifier"
        path.write_text(json.dumps(content))
    else:
        Path(content["token_file"]).write_text(sensitive + "\x00")
    result = subprocess.run(
        [sys.executable, "-m", "device_runtime.client_cli", "--config", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 2
    output = result.stdout + result.stderr
    assert sensitive not in output
    assert "Traceback" not in output
    assert "configuration or runtime unavailable" in result.stderr
