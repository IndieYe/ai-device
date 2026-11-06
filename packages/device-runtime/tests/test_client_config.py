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


@pytest.mark.parametrize("stage", ["directory", "token", "complete"])
def test_initialize_explicit_resume_preserves_interrupted_files(tmp_path, stage):
    from device_runtime.client_config import initialize

    directory = tmp_path / "config"
    directory.mkdir(mode=0o700)
    token = "test-only-resume-secret"
    arguments = {
        "server_url": "wss://example.test",
        "device_id": "client-one",
        "state_dir": tmp_path / "state",
        "token": token,
    }
    if stage in ("token", "complete"):
        (directory / "token").write_text(token + "\n")
        (directory / "token").chmod(0o600)
    if stage == "complete":
        (directory / "client.json").write_text(
            json.dumps(
                {
                    "server_url": arguments["server_url"],
                    "device_id": "client-one",
                    "state_dir": str(tmp_path / "state"),
                    "token_file": str(directory / "token"),
                }
            )
        )
        (directory / "client.json").chmod(0o600)
    before = {p.name: (p.stat().st_ino, p.read_bytes()) for p in directory.iterdir()}
    path = initialize(directory, **arguments, resume=True)
    assert load(path)[1] == token
    for name, snapshot in before.items():
        assert ((directory / name).stat().st_ino, (directory / name).read_bytes()) == snapshot
    assert not (tmp_path / "state").exists()


def test_initialize_resume_mismatch_preserves_token(tmp_path):
    from device_runtime.client_config import initialize

    directory = tmp_path / "config"
    directory.mkdir(mode=0o700)
    path = directory / "token"
    path.write_text("original-test-secret\n")
    path.chmod(0o600)
    with pytest.raises(ValueError):
        initialize(
            directory,
            server_url="wss://example.test",
            device_id="client-one",
            state_dir=tmp_path / "state",
            token="different-test-secret",
            resume=True,
        )
    assert path.read_text() == "original-test-secret\n"
    assert not (directory / "client.json").exists()


@pytest.mark.parametrize("kind", ["partial", "symlink", "public", "extra", "different"])
def test_initialize_resume_rejects_ambiguous_state(tmp_path, kind):
    from device_runtime.client_config import initialize

    directory = tmp_path / "config"
    args = {
        "server_url": "wss://example.test",
        "device_id": "client-one",
        "state_dir": tmp_path / "state",
        "token": "test-only-resume-secret",
    }
    initialize(directory, **args)
    if kind == "partial":
        (directory / "client.json").write_text("{")
    elif kind == "symlink":
        (directory / "alias").symlink_to(directory / "token")
    elif kind == "public":
        (directory / "token").chmod(0o644)
    elif kind == "extra":
        (directory / "unexpected").write_text("keep")
    else:
        args["device_id"] = "other-client"
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(ValueError):
        initialize(directory, **args, resume=True)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_setup_cli_explicit_resume_no_secret_output(tmp_path, monkeypatch, capsys):
    import io

    from device_runtime.client_setup import main

    directory = tmp_path / "config"
    directory.mkdir(mode=0o700)
    secret = "test-only-cli-resume-secret"
    (directory / "token").write_text(secret + "\n")
    (directory / "token").chmod(0o600)
    args = [
        "--config-dir",
        str(directory),
        "--state-dir",
        str(tmp_path / "state"),
        "--server-url",
        "wss://example.test",
        "--device-id",
        "client-one",
        "--token-stdin",
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO(secret + "\n"))
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert "--resume" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO(secret + "\n"))
    main([*args, "--resume"])
    output = capsys.readouterr()
    assert "Configured:" in output.out
    assert secret not in output.out + output.err


def test_initialize_cleanup_cannot_delete_successful_resume_token(tmp_path, monkeypatch):
    import os

    import device_runtime.client_config as config

    directory = tmp_path / "config"
    args = {
        "server_url": "wss://example.test",
        "device_id": "client-one",
        "state_dir": tmp_path / "state",
        "token": "test-only-concurrent-secret",
    }
    original = config.os.open
    resumed = []
    entered = False

    def interleaved_open(path, flags, *rest, **kwargs):
        nonlocal entered
        if str(path).endswith("client.json") and flags & os.O_CREAT and not entered:
            entered = True
            resumed.append(config.initialize(directory, **args, resume=True))
        return original(path, flags, *rest, **kwargs)

    monkeypatch.setattr(config.os, "open", interleaved_open)
    with pytest.raises(OSError):
        config.initialize(directory, **args)
    assert len(resumed) == 1
    assert load(resumed[0])[1] == args["token"]
