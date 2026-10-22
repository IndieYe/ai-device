"""Stopped-instance software lifecycle and portable, byte-preserving snapshots.

No process management, operation replay, state migration or credential persistence.
State format 1 is the only supported compatibility domain in this delivery.
"""

import asyncio
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
from contextlib import closing, contextmanager
from functools import wraps
from pathlib import Path

from . import installation as install
from .bundle import ROLES, BundleError, inspect_bundle, with_dependencies
from .installation import InstallationError
from .tasks import (
    OUTPUT_MAP_FILE,
    OUTPUT_MAP_FORMAT,
    output_map_metadata,
    read_output_map,
    validate_output_map,
)

PERSISTENT = ("instance.json", "config", "state", "run")
# Explicitly audited Device versions, NOT an inference from an installation format.
# Widen this map only after validating a new version's persistent data contract.
STATE_COMPATIBILITY = {"0.2.0": 1, "0.2.1": 1, "0.2.2": 1}


def _compatible(manifest):
    if STATE_COMPATIBILITY.get(manifest["components"]["device"]["version"]) != 1:
        raise InstallationError("Device version has no validated state-format-1 compatibility")


def _controlled(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (OSError, ValueError, KeyError, TypeError, BundleError, sqlite3.Error) as exc:
            if isinstance(exc, InstallationError):
                raise
            # Do not echo metadata, config, network responses or filesystem payloads.
            raise InstallationError(
                "invalid or unavailable lifecycle data; nothing activated"
            ) from exc

    return call


def _read(path):
    return json.loads(path.read_bytes())


def _root(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink() or not path.is_dir():
        raise InstallationError("instance directory is missing or is a symbolic link")
    return path.resolve()


def _release_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+!-]*", value):
        raise InstallationError("select a release ID, not a path")
    return value


def _tree(path):
    """Inventory regular files and directories without following any data symlink."""
    result = {}

    def visit(item, name):
        mode = item.lstat().st_mode
        if stat.S_ISDIR(mode):
            result[name] = {"kind": "directory"}
            for child in sorted(item.iterdir()):
                visit(child, name + "/" + child.name)
        elif stat.S_ISREG(mode):
            digest = hashlib.sha256()
            with item.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[name] = {
                "kind": "file",
                "size": item.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        else:
            raise InstallationError("snapshot data must contain only regular files and directories")

    visit(path, path.name)
    return result


def _record(active):
    if not isinstance(active, dict) or set(active) - {
        "activation_id",
        "task_output_map_sha256",
    } != {
        "format_version",
        "release",
        "roles",
        "release_version",
        "bundle_sha256",
    }:
        raise InstallationError("invalid installation record")
    _release_id(active["release"])
    if "activation_id" in active and not re.fullmatch(r"[0-9a-f]{32}", active["activation_id"]):
        raise InstallationError("invalid activation generation")
    if "task_output_map_sha256" in active and not re.fullmatch(
        r"[0-9a-f]{64}", active["task_output_map_sha256"]
    ):
        raise InstallationError("invalid task output map publication")
    roles = active["roles"]
    if (
        active["format_version"] != 1
        or not isinstance(roles, list)
        or not roles
        or any(not isinstance(r, str) or r not in ROLES for r in roles)
        or roles != sorted(set(roles))
        or not isinstance(active["bundle_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", active["bundle_sha256"])
        or not isinstance(active["release_version"], str)
    ):
        raise InstallationError("unsupported installation format or roles")
    return active


def _persistent(root, roles):
    for name in PERSISTENT:
        _tree(root / name)
    identity = _read(root / "instance.json")
    if identity["format_version"] != 1:
        raise InstallationError("unsupported identity/state format")
    install._identity(identity["instance_id"])
    config = _read(root / "config" / "roles.json")
    if not isinstance(config, dict) or set(config) != set(roles):
        raise InstallationError("persistent role configuration does not match installed roles")
    for role in roles:
        if not isinstance(config[role], dict) or not (root / "state" / role).is_dir():
            raise InstallationError("persistent state/config is missing; use backup recovery")
        if role == "center":
            port = config[role]["port"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise InstallationError("invalid center configuration")
        else:
            from urllib.parse import urlsplit

            from .client import device_url

            if config[role]["device_id"] != identity["instance_id"]:
                raise InstallationError("configured device identity does not match instance")
            parts = urlsplit(config[role]["server_url"])
            allowed = ("ws", "wss") if role == "client" else ("http", "https")
            if parts.scheme not in allowed:
                raise InstallationError("invalid role server URL")
            device_url(
                parts._replace(scheme="wss" if parts.scheme in ("https", "wss") else "ws").geturl(),
                identity["instance_id"],
            )
    if not (root / "run").is_file() or (root / "run").read_text() != install.RUNNER:
        raise InstallationError("stable entrypoint is missing or incompatible")
    mapping = read_output_map(root)
    if mapping is not None and "client" not in roles:
        raise InstallationError("task output map requires a client role")
    return identity


def _layout(root):
    # Validate before obtaining a lease: never create a lock in an unrelated directory.
    if not (root / ".installation.lock").is_file() or (root / ".installation.lock").is_symlink():
        raise InstallationError("installation lease is missing or invalid")
    if (root / "active.json").is_symlink() or (root / "releases").is_symlink():
        raise InstallationError("invalid active pointer/release directory")
    active = _record(_read(root / "active.json"))
    identity = _persistent(root, active["roles"])
    return active, identity


def _check_release(root, release, roles, *, environments=True):
    directory = root / "releases" / _release_id(release)
    if directory.is_symlink() or not directory.is_dir():
        raise InstallationError("release directory is missing or invalid")
    if (directory / "installation.json").is_symlink():
        raise InstallationError("invalid installation record")
    record = _record(_read(directory / "installation.json"))
    if record["release"] != release or record["roles"] != roles:
        raise InstallationError("release role contract does not match")
    _tree(directory / "bundle")
    manifest = inspect_bundle(directory / "bundle")
    install._check_target(manifest, roles)
    install._client_contract(directory / "bundle", manifest)
    if (
        install._fingerprint(manifest) != record["bundle_sha256"]
        or manifest["release_version"] != record["release_version"]
    ):
        raise InstallationError("release bundle does not match installation record")
    _compatible(manifest)
    if environments:
        required_map_format = install._required_output_map_format(root)
        for role in roles:
            home = directory / role
            if home.is_symlink() or (home / "venv").is_symlink():
                raise InstallationError("invalid role environment directory")
            python = home / "venv" / "bin" / "python"
            if not python.is_file():
                raise InstallationError("role interpreter is unavailable")
            install._verify_environment(
                python,
                role,
                manifest,
                cwd=home,
                log=None,
                required_output_map_format=required_map_format,
            )
    return record


def _software_record(active):
    return {k: v for k, v in active.items() if k not in ("activation_id", "task_output_map_sha256")}


def _publish(root, candidate):
    pointer = {**candidate, "activation_id": uuid.uuid4().hex}
    current = _read(root / "active.json")
    if "task_output_map_sha256" in current:
        pointer["task_output_map_sha256"] = current["task_output_map_sha256"]
    install._json(root / "active.json", pointer)
    return pointer


def _stopped(value):
    if not value:
        raise InstallationError(
            "explicit --stopped confirmation required; this program never stops processes"
        )
    install._supported_host()


def _binding(root, active, identity):
    return {
        "active": active,
        "identity": identity,
        "identity_sha256": _tree(root / "instance.json")["instance.json"]["sha256"],
        "config_sha256": _tree(root / "config" / "roles.json")["roles.json"]["sha256"],
        "state_format": 1,
    }


@_controlled
@with_dependencies
def prepare_upgrade(root, bundle, *, stopped=False):
    _stopped(stopped)
    root = _root(root)
    _layout(root)
    with install._lock(root):
        active, identity = _layout(root)
        if _check_release(
            root, active["release"], active["roles"], environments=False
        ) != _software_record(active):
            raise InstallationError("active pointer does not match installation")
        bundle = Path(bundle).resolve()
        _tree(bundle)
        manifest = inspect_bundle(bundle)
        _compatible(manifest)
        binding = _binding(root, active, identity)
        candidate = install._prepare(root, bundle, manifest, active["roles"])
        install._json(
            root / "releases" / candidate["release"] / "candidate.json",
            {"format_version": 1, "binding": binding, "candidate": candidate},
        )
        return {"status": "prepared", **candidate}


@_controlled
def activate_release(root, release, *, stopped=False):
    _stopped(stopped)
    root = _root(root)
    _layout(root)
    with install._lock(root):
        active, identity = _layout(root)
        directory = root / "releases" / _release_id(release)
        if directory.is_symlink() or (directory / "candidate.json").is_symlink():
            raise InstallationError("invalid candidate directory")
        prepared = _read(directory / "candidate.json")
        if prepared["format_version"] != 1 or prepared["binding"] != _binding(
            root, active, identity
        ):
            raise InstallationError(
                "stale candidate; prepare again against current active instance"
            )
        candidate = _check_release(root, release, active["roles"])
        if candidate != prepared["candidate"]:
            raise InstallationError("candidate installation record changed")
        pointer = _publish(root, candidate)
        return {"status": "activated", **pointer}


@_controlled
def rollback_release(root, release, *, stopped=False):
    _stopped(stopped)
    root = _root(root)
    _layout(root)
    with install._lock(root):
        active, _ = _layout(root)
        candidate = _check_release(root, release, active["roles"])
        # Format 1 only. No data rollback or migration is performed or implied.
        pointer = _publish(root, candidate)
        return {"status": "software_rollback", "state_format": 1, **pointer}


@contextmanager
def _diagnostic_lease(root):
    path = root / ".installation.lock"
    if path.is_symlink() or not path.is_file():
        raise InstallationError("installation lease is missing or invalid")
    with path.open("rb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status = "free"
        except BlockingIOError:
            status = "shared_occupied"
        # If an exclusive owner exists this fails non-blockingly. Once obtained,
        # keep the shared lease for all reads; never drop it to probe lock state.
        fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        yield status


async def _health(url, token_env):
    try:
        from .http_client import DeviceAPI

        async with DeviceAPI(url, os.environ.get(token_env, "")) as api:
            await asyncio.wait_for(api.list_devices(limit=1), timeout=10)
        return {"status": "healthy", "scope": "center_authenticated_read_only"}
    except (ImportError, ValueError, RuntimeError, OSError):
        return {"status": "unavailable", "scope": "center_authenticated_read_only"}


def inspect_instance(root, *, health_url=None, token_env="DEVICE_RUNTIME_ADMIN_TOKEN"):
    result = {
        "format_version": 1,
        "installation": "unavailable",
        "persistent": "unavailable",
        "environments": "not_checked",
        "lock": "unavailable",
        "online_health": {"status": "not_checked"},
    }
    try:
        root = _root(root)
        # Existence is NOT evidence that installed imports/dependency closure work.
        with _diagnostic_lease(root) as lock_status:
            result["lock"] = lock_status
            active, _ = _layout(root)
            result["persistent"] = "complete"
            result.update(
                {k: active[k] for k in ("release", "roles", "release_version", "bundle_sha256")}
            )
            if _check_release(
                root, active["release"], active["roles"], environments=False
            ) != _software_record(active):
                raise InstallationError("bad active pointer")
            manifest = _read(root / "releases" / active["release"] / "bundle" / "manifest.json")
            result["component_versions"] = {
                component: entry["version"] for component, entry in manifest["components"].items()
            }
            result["installation"] = "complete"
            result["environments"] = "unavailable"
            _check_release(root, active["release"], active["roles"])
            result["environments"] = "available"
    except BlockingIOError:
        result["lock"] = "exclusive_occupied"
    except (OSError, ValueError, KeyError, TypeError, BundleError):
        pass
    if health_url is not None:
        result["online_health"] = asyncio.run(_health(health_url, token_env))
    return result


def _new_destination(destination, source):
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise InstallationError("destination must be new and nonexistent")
    destination = destination.resolve()
    if (
        destination == source
        or destination.is_relative_to(source)
        or source.is_relative_to(destination)
    ):
        raise InstallationError("source and destination must not overlap")
    # A new leaf inside some other existing instance is not an isolated target.
    for ancestor in destination.parents:
        if (ancestor / "instance.json").exists() or (ancestor / "active.json").exists():
            raise InstallationError("destination must not be inside an existing instance")
    return destination


def _snapshot_inventory(root):
    result = {}
    for name in (*PERSISTENT, "bundle"):
        result.update(_tree(root / name))
    return result


def _private(path):
    path.chmod(0o700 if path.is_dir() else 0o600)
    if path.is_dir():
        for item in path.rglob("*"):
            item.chmod(0o700 if item.is_dir() else 0o600)


def _copy(source, target):
    _tree(source)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target, follow_symlinks=False)
    _tree(target)
    _private(target)


def _sqlite_check(snapshot, *, output_root=None, inherited=None):
    # Open only a disposable full copy: SQLite can create SHM even with mode=ro.
    # WAL and SHM are never omitted; no checkpoint is issued against live data.
    paths = {}
    inherited = validate_output_map(inherited or {})
    with tempfile.TemporaryDirectory(prefix="device-snapshot-check-") as temporary:
        target = Path(temporary) / "state"
        shutil.copytree(snapshot / "state", target, symlinks=True)
        _tree(target)
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            with path.open("rb") as stream:
                header = stream.read(16)
            relative = path.relative_to(target)
            formal = relative.as_posix() in {
                "center/operations.sqlite3",
                "mcp/operations.sqlite3",
                "client/operations.sqlite3",
                "client/tasks.sqlite3",
            }
            if header != b"SQLite format 3\x00":
                if formal:
                    raise InstallationError("formal journal database header is damaged")
                continue
            if formal and path.stat().st_size < 100:
                raise InstallationError("formal journal database is truncated")
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise InstallationError("SQLite snapshot failed integrity validation")
                if relative.as_posix() == "client/tasks.sqlite3":
                    # Bound by retained receipt paths, not restore generations.
                    # Read only this WAL-inclusive disposable state copy.
                    for (raw,) in db.execute("SELECT result FROM operations"):
                        stored = json.loads(raw or "{}").get("output_path")
                        if not isinstance(stored, str):
                            continue
                        paths[stored] = None  # Account for every non-null receipt reference.
                        if stored in inherited:
                            paths[stored] = inherited[stored]
                        elif output_root is not None:
                            original = Path(stored)
                            if original.is_absolute() and original.is_relative_to(output_root):
                                paths[stored] = original.relative_to(output_root).as_posix()
    return validate_output_map(paths)


@_controlled
def backup_instance(root, destination, *, stopped=False):
    _stopped(stopped)
    root = _root(root)
    _layout(root)
    destination = _new_destination(destination, root)
    with install._lock(root):
        active, _ = _layout(root)
        if _check_release(
            root, active["release"], active["roles"], environments=False
        ) != _software_record(active):
            raise InstallationError("active installation record mismatch")
        bundle = root / "releases" / active["release"] / "bundle"
        before = {}
        for name in PERSISTENT:
            before.update(_tree(root / name))
        before.update(_tree(bundle))
        inherited = read_output_map(root)
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        for name in PERSISTENT:
            _copy(root / name, destination / name)
        _copy(bundle, destination / "bundle")
        after = {}
        for name in PERSISTENT:
            after.update(_tree(root / name))
        after.update(_tree(bundle))
        if (
            before != after
            or before != _snapshot_inventory(destination)
            or _read(root / "active.json") != active
            or read_output_map(root) != inherited
        ):
            raise InstallationError(
                "source changed during stopped snapshot; incomplete backup retained"
            )
        task_outputs = _sqlite_check(
            destination, output_root=root / "state" / "client" / "output", inherited=inherited
        )
        manifest = {
            "format_version": 1,
            "state_format": 1,
            "active": active,
            "inventory": before,
            "task_outputs": task_outputs,
        }
        install._json(destination / "backup.json", manifest)
        (destination / "backup.json").chmod(0o600)
        # Completion is deliberately last; failure products cannot be restored.
        install._json(
            destination / "complete.json",
            {
                "format_version": 1,
                "manifest_sha256": hashlib.sha256(
                    (destination / "backup.json").read_bytes()
                ).hexdigest(),
            },
        )
        (destination / "complete.json").chmod(0o600)
        return {
            "status": "backup_complete",
            "destination": str(destination),
            "bundle_sha256": active["bundle_sha256"],
        }


def _validate_backup(root):
    _tree(root)
    manifest = _read(root / "backup.json")
    complete = _read(root / "complete.json")
    if (
        complete["format_version"] != 1
        or complete["manifest_sha256"]
        != hashlib.sha256((root / "backup.json").read_bytes()).hexdigest()
        or manifest["format_version"] != 1
        or manifest["state_format"] != 1
        or {p.name for p in root.iterdir()}
        != {*PERSISTENT, "bundle", "backup.json", "complete.json"}
        or manifest["inventory"] != _snapshot_inventory(root)
    ):
        raise InstallationError("backup is incomplete, damaged or unsupported")
    active = _record(manifest["active"])
    _persistent(root, active["roles"])
    bundle = inspect_bundle(root / "bundle")
    install._check_target(bundle, active["roles"])
    install._client_contract(root / "bundle", bundle)
    _compatible(bundle)
    if (
        install._fingerprint(bundle) != active["bundle_sha256"]
        or bundle["release_version"] != active["release_version"]
    ):
        raise InstallationError("backup bundle does not match active software")
    task_outputs = validate_output_map(manifest["task_outputs"])
    if _sqlite_check(root, inherited=task_outputs) != task_outputs:
        raise InstallationError("backup task output map does not match retained receipts")
    return manifest, bundle


@_controlled
def restore_instance(backup, destination):
    install._supported_host()
    backup = _root(backup)
    destination = _new_destination(destination, backup)
    manifest, bundle = _validate_backup(backup)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    # No active pointer exists on any failed preparation/validation path.
    with install._lock(destination):
        for name in PERSISTENT:
            _copy(backup / name, destination / name)
        (destination / "run").chmod(0o700)
        candidate = install._prepare(
            destination,
            backup / "bundle",
            bundle,
            manifest["active"]["roles"],
            required_output_map_format=(
                OUTPUT_MAP_FORMAT if "client" in manifest["active"]["roles"] else None
            ),
        )
        restored = {}
        for name in PERSISTENT:
            restored.update(_tree(destination / name))
        expected = {k: v for k, v in manifest["inventory"].items() if k.split("/")[0] in PERSISTENT}
        if restored != expected:
            raise InstallationError("restored persistent data does not match backup")
        _persistent(destination, candidate["roles"])
        _check_release(destination, candidate["release"], candidate["roles"])
        # Revalidate the source as well; never publish from a changing backup.
        current, _ = _validate_backup(backup)
        if current != manifest:
            raise InstallationError("backup changed during recovery")
        if "client" in candidate["roles"]:
            install._json(
                destination / OUTPUT_MAP_FILE,
                output_map_metadata(destination, manifest["task_outputs"]),
            )
            (destination / OUTPUT_MAP_FILE).chmod(0o600)
            candidate = {
                **candidate,
                "task_output_map_sha256": hashlib.sha256(
                    (destination / OUTPUT_MAP_FILE).read_bytes()
                ).hexdigest(),
            }
        install._json(destination / "active.json", candidate)
        (destination / "active.json").chmod(0o600)
        return {"status": "restored_not_started", "instance_root": str(destination), **candidate}


def add_lifecycle_parsers(actions):
    inspect = actions.add_parser(
        "inspect", help="Read-only installation, lease and optional center health diagnostics"
    )
    inspect.add_argument("--root", type=Path, required=True)
    inspect.add_argument(
        "--health-url", help="Explicit authenticated center GET; no operations submitted"
    )
    inspect.add_argument("--token-env", default="DEVICE_RUNTIME_ADMIN_TOKEN")
    for name in ("prepare", "activate", "rollback", "backup"):
        parser = actions.add_parser(name, help="Stopped-instance lifecycle; never stops a process")
        parser.add_argument("--root", type=Path, required=True)
        parser.add_argument(
            "--stopped",
            action="store_true",
            help="Confirm ALL instance processes, including direct launches, are stopped",
        )
        if name == "prepare":
            parser.add_argument("--bundle", type=Path, required=True)
            parser.add_argument("--dependency-dir", type=Path)
        elif name in ("activate", "rollback"):
            parser.add_argument("--release", required=True)
        else:
            parser.add_argument("--destination", type=Path, required=True)
    restore = actions.add_parser(
        "restore", help="Rebuild a NEW offline target from a complete backup; never starts/replays"
    )
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)


def run_lifecycle(args):
    action = args.instance_action
    if action == "inspect":
        return inspect_instance(args.root, health_url=args.health_url, token_env=args.token_env)
    if action == "prepare":
        return prepare_upgrade(
            args.root,
            args.bundle,
            stopped=args.stopped,
            dependency_dir=getattr(args, "dependency_dir", None),
        )
    if action == "activate":
        return activate_release(args.root, args.release, stopped=args.stopped)
    if action == "rollback":
        return rollback_release(args.root, args.release, stopped=args.stopped)
    if action == "backup":
        return backup_instance(args.root, args.destination, stopped=args.stopped)
    return restore_instance(args.backup, args.destination)
