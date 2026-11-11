"""Installed client configuration; no release pointers or installer dependencies."""

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

from pydantic import TypeAdapter

from .client import device_url
from .limits import DEFAULT_CONCURRENCY
from .protocol import Identifier

LIMITS = ("max_concurrent_requests", "max_concurrent_queries", "max_active_tasks")
REQUIRED = {"server_url", "device_id", "state_dir", "token_file"}


def _read_regular(path, limit, *, private=False):
    # O_NONBLOCK prevents an accidental FIFO from hanging before fstat.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("configuration input must be a regular file")
        if private and (info.st_mode & 0o077 or info.st_uid not in (0, os.getuid())):
            raise ValueError("token ownership or permissions are invalid")
        value = stream.read(limit + 1)
        if len(value) > limit:
            raise ValueError("configuration input is too large")
        return value


def load(path):
    """Read explicit configuration and a private token file before creating state.

    Paths must be absolute so launchd/systemd working directories cannot change
    the identity or journal being used. The service manager selects the OS user;
    configuration cannot request a different identity or privilege level.
    """
    try:
        config_path = Path(path)
        config = json.loads(_read_regular(config_path, 65536))
        if not isinstance(config, dict) or set(config) - REQUIRED - set(LIMITS):
            raise ValueError("invalid configuration fields")
        if not REQUIRED <= config.keys():
            raise ValueError("missing configuration fields")
        TypeAdapter(Identifier).validate_python(config["device_id"])
        device_url(config["server_url"], config["device_id"])
        for field in ("state_dir", "token_file"):
            if not isinstance(config[field], str) or not Path(config[field]).is_absolute():
                raise ValueError("paths must be absolute")
        limits = {name: config.get(name, DEFAULT_CONCURRENCY) for name in LIMITS}
        if any(type(value) is not int or value < 1 for value in limits.values()):
            raise ValueError("invalid concurrency limits")
        # Credentials never enter argv, a plist, or an exception's public text.
        token = _read_regular(config["token_file"], 4098, private=True).rstrip("\r\n")
        if len(token) < 16 or len(token) > 4096 or not token.isascii() or not token.isprintable():
            raise ValueError("invalid token")
        return SimpleNamespace(
            server_url=config["server_url"],
            device_id=config["device_id"],
            state_dir=Path(config["state_dir"]),
            maintenance=True,
            **limits,
        ), token
    except (ValueError, TypeError, OSError, AttributeError) as exc:
        raise ValueError("client configuration or private token file is invalid") from exc


def initialize(directory, *, server_url, device_id, state_dir, token, resume=False):
    """Create private configuration, or explicitly complete matching missing files.

    This intentionally does not select a service user, start a service, mutate
    existing configuration or create business state. Run as the intended user;
    the platform installer separately registers that user's service.
    """
    directory = Path(directory).absolute()
    state_dir = Path(state_dir)
    device_url(server_url, device_id)
    if not state_dir.is_absolute():
        raise ValueError("state directory must be absolute")
    if not isinstance(token, str) or not 16 <= len(token) <= 4096:
        raise ValueError("invalid token")
    if not token.isascii() or not token.isprintable():
        raise ValueError("invalid token")
    value = {
        "server_url": server_url,
        "device_id": device_id,
        "state_dir": str(state_dir),
        "token_file": str(directory / "token"),
    }
    if resume:
        return _resume_initialization(directory, value, token)
    # mkdir is the no-overwrite boundary; a concurrent initializer cannot win
    # and then have its configuration overwritten by this invocation.
    directory.mkdir(mode=0o700)
    # Retain even incomplete files: another explicit resume may already have
    # validated them. Cleanup here could invalidate its successful result.
    for name, text in (("token", token + "\n"), ("client.json", json.dumps(value) + "\n")):
        path = directory / name
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    return directory / "client.json"


def _resume_initialization(directory, value, token):
    """Complete only missing files; preserve every existing byte on failure."""
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("incomplete configuration directory is not private")
    if {p.name for p in directory.iterdir()} - {"token", "client.json"}:
        raise ValueError("configuration contains unrecognized files; explicit inspection required")
    expected = (("token", token + "\n"), ("client.json", json.dumps(value) + "\n"))
    # Validate ALL existing files before creating anything. Symlinks and damaged
    # partial writes remain for explicit inspection, never overwritten or removed.
    missing = []
    for name, text in expected:
        path = directory / name
        try:
            existing = path.lstat()
        except FileNotFoundError:
            missing.append((name, text))
            continue
        if (
            not stat.S_ISREG(existing.st_mode)
            or existing.st_uid != os.getuid()
            or existing.st_mode & 0o077
            or existing.st_nlink != 1
        ):
            raise ValueError("incomplete configuration file is not private")
        content = _read_regular(path, 65536, private=True)
        if name == "token":
            matches = content == text
        else:
            matches = json.loads(content) == value
        if not matches:
            raise ValueError("existing configuration differs; resume refused")
    for name, text in missing:
        path = directory / name
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    load(directory / "client.json")
    return directory / "client.json"
