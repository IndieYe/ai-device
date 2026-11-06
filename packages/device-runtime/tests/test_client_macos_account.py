import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from device_runtime.client_macos_account import (
    MacAccountError,
    resolve_account,
    validate_registration_files,
)


def selected(**changes):
    values = {
        "pw_name": "alice",
        "pw_uid": 501,
        "pw_gid": 20,
        "pw_dir": "/Users/alice",
        "pw_shell": "/bin/zsh",
    }
    values.update(changes)
    return resolve_account("alice", lookup=lambda name: SimpleNamespace(**values))


def test_resolve_ignores_environment(monkeypatch):
    monkeypatch.setenv("HOME", "/var/root")
    monkeypatch.setenv("USER", "root")
    account = selected()
    assert account.name == "alice"
    assert account.gid == 20
    assert account.layout.home == Path("/Users/alice")


@pytest.mark.parametrize("name", [None, "", "-alice", "a b", "a\n", "a\x00b"])
def test_missing_or_invalid_name_never_looks_up(name):
    def lookup(_):
        pytest.fail("invalid account must not be looked up")

    with pytest.raises(MacAccountError):
        resolve_account(name, lookup=lookup)


@pytest.mark.parametrize(
    "changes",
    [
        {"pw_uid": 0},
        {"pw_uid": 500},
        {"pw_shell": "/usr/bin/false"},
        {"pw_shell": "/usr/sbin/nologin"},
        {"pw_name": "bob"},
        {"pw_gid": -1},
        {"pw_dir": "relative"},
        {"pw_dir": "/var/root"},
    ],
)
def test_rejects_unsupported_account(changes):
    with pytest.raises(MacAccountError):
        selected(**changes)


@pytest.mark.parametrize("error", [KeyError("sensitive"), OSError("sensitive")])
def test_lookup_failure_is_sanitized(error):
    def lookup(_):
        raise error

    with pytest.raises(MacAccountError, match="unavailable") as caught:
        resolve_account("alice", lookup=lookup)
    assert "sensitive" not in str(caught.value)


@pytest.fixture
def filesystem(monkeypatch):
    account = selected()
    layout = account.layout
    files = {}
    for path in (Path("/"), Path("/Users")):
        files[path] = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
    for path in (layout.home, layout.home / "Library", layout.directory.parent):
        files[path] = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=501)
    files[layout.directory] = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=501)
    for path in (layout.config, layout.token):
        files[path] = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=501)

    def lstat(path):
        if path not in files:
            raise FileNotFoundError()
        return files[path]

    monkeypatch.setattr(Path, "lstat", lstat)
    return account, files


def test_preflight_does_not_read_credentials_or_write(filesystem, monkeypatch):
    account, _ = filesystem

    def forbidden(*args, **kwargs):
        pytest.fail("preflight must not read or write file contents")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    validate_registration_files(account)


@pytest.mark.parametrize(
    "field,mode,uid",
    [
        ("config", stat.S_IFREG | 0o644, 501),
        ("token", stat.S_IFREG | 0o600, 0),
        ("token", stat.S_IFIFO | 0o600, 501),
        ("config", stat.S_IFLNK | 0o777, 501),
        ("directory", stat.S_IFDIR | 0o770, 501),
        ("home", stat.S_IFDIR | 0o755, 0),
        ("home", stat.S_IFLNK | 0o777, 501),
    ],
)
def test_bad_paths_rejected(filesystem, field, mode, uid):
    account, files = filesystem
    files[getattr(account.layout, field)] = SimpleNamespace(st_mode=mode, st_uid=uid)
    with pytest.raises(MacAccountError):
        validate_registration_files(account)


@pytest.mark.parametrize("mode", [stat.S_IFREG | 0o600, stat.S_IFLNK | 0o777])
def test_existing_registration_not_overwritten(filesystem, mode):
    account, files = filesystem
    files[account.layout.plist] = SimpleNamespace(st_mode=mode, st_uid=501)
    with pytest.raises(MacAccountError, match="will not overwrite"):
        validate_registration_files(account)


def test_missing_token_fails_closed(filesystem):
    account, files = filesystem
    del files[account.layout.token]
    with pytest.raises(MacAccountError, match="unavailable"):
        validate_registration_files(account)


@pytest.mark.parametrize("field", ["launchagents", "logs", "log_parent", "state", "log"])
def test_existing_destination_symlink_rejected(filesystem, field):
    account, files = filesystem
    layout = account.layout
    paths = {
        "launchagents": layout.plist.parent,
        "logs": layout.home / "Library/Logs",
        "log_parent": layout.log.parent,
        "state": layout.state,
        "log": layout.log,
    }
    files[paths[field]] = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=501)
    with pytest.raises(MacAccountError):
        validate_registration_files(account)


def test_destination_access_failure_is_not_absence(filesystem, monkeypatch):
    account, _files = filesystem
    original = Path.lstat

    def lstat(path):
        if path == account.layout.plist.parent:
            raise PermissionError("private details")
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(MacAccountError, match="destination is unavailable"):
        validate_registration_files(account)


def test_config_probe_uses_exact_uid_and_no_output():
    import subprocess

    from device_runtime.client_macos_account import validate_config_as_user

    account = selected(pw_dir="/Users/a b;$value")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    validate_config_as_user(account, run=run)
    argv, kwargs = calls[0]
    assert argv[:7] == [
        "/usr/bin/sudo",
        "-n",
        "-H",
        "-u",
        "#501",
        "--",
        "/opt/ai-device-client/runtime/bin/python3",
    ]
    assert argv[7:10] == ["-I", "-B", "-c"]
    assert str(account.layout.home) not in argv[10]
    assert argv[-4:] == [
        str(account.layout.config),
        str(account.layout.state),
        str(account.layout.token),
        "501",
    ]
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "timeout": 15,
        "check": False,
    }


@pytest.mark.parametrize("returncode", [1, 2, -15])
def test_config_probe_failure_is_closed(returncode):
    from device_runtime.client_macos_account import validate_config_as_user

    with pytest.raises(MacAccountError, match="configuration check failed"):
        validate_config_as_user(
            selected(), run=lambda *args, **kwargs: SimpleNamespace(returncode=returncode)
        )


def test_config_probe_timeout_does_not_print_subprocess_details():
    import subprocess

    from device_runtime.client_macos_account import validate_config_as_user

    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("sensitive", 15, output="credential")

    with pytest.raises(MacAccountError) as caught:
        validate_config_as_user(selected(), run=run)
    assert "sensitive" not in str(caught.value)
    assert "credential" not in str(caught.value)


@pytest.mark.parametrize(
    "fault", [None, "missing", "symlink", "writable", "owner", "executable", "hardlink", "parent"]
)
def test_fixed_launcher_preflight(monkeypatch, fault):
    from device_runtime import client_macos_account as module

    def inspect(path):
        if path == module.LAUNCHER:
            if fault == "missing":
                raise MacAccountError("installed file unavailable")
            mode = stat.S_IFREG | 0o755
            if fault == "symlink":
                mode = stat.S_IFLNK | 0o755
            elif fault == "writable":
                mode |= 0o020
            elif fault == "executable":
                mode = stat.S_IFREG | 0o644
            return SimpleNamespace(
                st_mode=mode,
                st_uid=501 if fault == "owner" else 0,
                st_nlink=2 if fault == "hardlink" else 1,
            )
        return SimpleNamespace(
            st_mode=stat.S_IFDIR | (0o777 if fault == "parent" else 0o755), st_uid=0
        )

    monkeypatch.setattr(module, "_inspect", inspect)
    if fault is None:
        module.validate_launcher()
    else:
        with pytest.raises(MacAccountError):
            module.validate_launcher()


@pytest.mark.parametrize("size", [102, 103, 104, 105])
@pytest.mark.parametrize("unicode_home", [False, True])
def test_control_socket_path_byte_boundary(size, unicode_home):
    from device_runtime.client_macos_account import validate_control_socket_path

    prefix = "/Users/" + ("测" if unicode_home else "a")
    suffix = "/Library/Application Support/ai-device-client/state/maintenance.sock"
    home = prefix + "a" * (size - len((prefix + suffix).encode()))
    account = selected(pw_dir=home)
    assert len(os.fsencode(account.layout.state / "maintenance.sock")) == size
    if size >= 104:
        with pytest.raises(MacAccountError, match="socket path"):
            validate_control_socket_path(account)
    else:
        validate_control_socket_path(account)


def test_long_socket_preflight_does_not_inspect_files(monkeypatch):
    from device_runtime import client_macos_account as module

    def forbidden(*args):
        pytest.fail("path rejection must precede filesystem inspection")

    monkeypatch.setattr(module, "_inspect", forbidden)
    with pytest.raises(MacAccountError, match="socket path"):
        validate_registration_files(selected(pw_dir="/Users/" + "a" * 80))


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin sockaddr_un limit")
@pytest.mark.parametrize("size", [103, 104])
def test_darwin_socket_bind_matches_preflight(size):
    import socket
    import tempfile

    from device_runtime.client_macos_account import validate_control_socket_path

    with tempfile.TemporaryDirectory(prefix="ds-", dir="/tmp") as directory:
        suffix = "/Library/Application Support/ai-device-client/state/maintenance.sock"
        home = directory + "/" + "a" * (size - len(os.fsencode(directory + "/" + suffix)))
        account = selected(pw_dir=home)
        account.layout.state.mkdir(parents=True)
        path = account.layout.state / "maintenance.sock"
        assert len(os.fsencode(path)) == size
        with socket.socket(socket.AF_UNIX) as connection:
            if size == 103:
                validate_control_socket_path(account)
                connection.bind(str(path))
            else:
                with pytest.raises(MacAccountError, match="socket path"):
                    validate_control_socket_path(account)
                with pytest.raises(OSError):
                    connection.bind(str(path))
