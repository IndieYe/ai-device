"""Mac role installation. Prepare immutable environments, then publish one pointer.

This module does not start services, replay operations, or write persistent state.
Failed candidates are retained for inspection and never become active.
"""

import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from contextlib import contextmanager, nullcontext
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlsplit

from packaging.requirements import Requirement

from .bundle import COMPONENTS, ROLES, inspect_bundle, with_dependencies


class InstallationError(ValueError):
    """An installation failed without changing an active release."""


# Deliberately stdlib-only: rebuilding cannot depend on an intact active venv.
RUNNER = """#!/usr/bin/env python3
import fcntl
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import signal
import re

root = Path(__file__).resolve().parent
try:
    if len(sys.argv) != 2:
        raise ValueError("usage: run <center|mcp|client>")
    role = sys.argv[1]
    with (root / ".installation.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        active = json.loads((root / "active.json").read_text())
        config = json.loads((root / "config" / "roles.json").read_text())
        if role not in active["roles"]:
            raise ValueError("role is not installed")
        release = active["release"]
        if not isinstance(release, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+!-]*", release):
            raise ValueError("invalid active release")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        venv = root / "releases" / release / role / "venv"
        if role == "mcp":
            env["DEVICE_RUNTIME_API_URL"] = config[role]["server_url"]
            argv = [str(venv / "bin" / "python"), "-m", "device_runtime.mcp_server"]
        else:
            argv = [str(venv / "bin" / "python"), "-m", "device_runtime.cli",
                    "server" if role == "center" else "client",
                    "--state-dir", str(root / "state" / role)]
            if role == "center":
                argv += ["--host", "127.0.0.1", "--port", str(config[role]["port"])]
            else:
                argv += ["--server-url", config[role]["server_url"],
                         "--device-id", config[role]["device_id"]]
        if role == "client":
            for name in ("max_concurrent_requests", "max_concurrent_queries", "max_active_tasks"):
                if name in config[role]:
                    value = config[role][name]
                    if type(value) is not int or value < 1:
                        raise ValueError("concurrency limits must be positive integers")
                    argv += ["--" + name.replace("_", "-"), str(value)]
        # Install handlers BEFORE spawning: signals received while Popen has not
        # returned are queued until its child handle becomes available.
        process = None
        pending_signals = []
        def forward(signum, frame):
            if process is None:
                pending_signals.append(signum)
            elif process.poll() is None:
                process.send_signal(signum)
        signal.signal(signal.SIGTERM, forward)
        signal.signal(signal.SIGINT, forward)
        process = subprocess.Popen(argv, cwd=root, env=env, pass_fds=(lock.fileno(),))
        for signum in pending_signals:
            forward(signum, None)
        raise SystemExit(process.wait())
except (OSError, ValueError, KeyError, TypeError):
    print("device instance: invalid configuration, unavailable environment or installation busy", file=sys.stderr)
    raise SystemExit(2)
"""


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise InstallationError("instance ID must be 1-64 simple identifier characters")
    return value


def _fingerprint(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def _json(path, value):
    # Same-directory replacement is atomic. No journal/config data passes here.
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _lock(root):
    with (root / ".installation.lock").open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InstallationError(
                "instance is running or another installation is active"
            ) from exc
        yield


def _client_contract(bundle, manifest):
    """The client extra must select exactly the bundled core versions, not public lookalikes."""
    path = bundle / manifest["components"]["device"]["path"]
    with zipfile.ZipFile(path) as archive:
        metadata = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        message = BytesParser().parsebytes(archive.read(metadata))
    if "client" not in message.get_all("Provides-Extra", []):
        raise InstallationError("device must provide the client extra")
    requirements = [Requirement(raw) for raw in message.get_all("Requires-Dist", [])]
    for component in ("bash", "fs"):
        name = COMPONENTS[component][0]
        version = manifest["components"][component]["version"]
        matches = [r for r in requirements if re.sub(r"[-_.]+", "-", r.name).lower() == name]
        if len(matches) != 1:
            raise InstallationError("device client extra must declare both exact core dependencies")
        requirement = matches[0]
        if (
            str(requirement.specifier) != f"=={version}"
            or requirement.url
            or requirement.extras
            or not requirement.marker
            or not requirement.marker.evaluate({"extra": "client"})
            or any(requirement.marker.evaluate({"extra": e}) for e in ("", "server", "mcp"))
        ):
            raise InstallationError("core dependency must match the bundle and be client-only")


def _invoke(argv, *, cwd, log):
    env = {k: v for k, v in os.environ.items() if not k.startswith("UV_")}
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    # Lifecycle diagnostics pass no log: read-only probes must not create files.
    with log.open("ab") if log is not None else nullcontext(subprocess.DEVNULL) as stream:
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                stdout=stream,
                stderr=stream,
                check=False,
                timeout=60 if log is None else None,
            )
        except subprocess.TimeoutExpired as exc:
            raise InstallationError("environment verification timed out") from exc
    if result.returncode:
        raise InstallationError(
            f"environment preparation failed ({argv[2]}); log: {log}; active release remains unchanged"
        )


def _required_output_map_format(root):
    from .tasks import OUTPUT_MAP_FORMAT, read_output_map

    try:
        return OUTPUT_MAP_FORMAT if read_output_map(root) is not None else None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InstallationError("restored task output map is missing or invalid") from exc


def _verify_environment(python, role, manifest, *, cwd, log, required_output_map_format=None):
    expected = {
        manifest["components"][c]["distribution"]: manifest["components"][c]["version"]
        for c in ROLES[role]["components"]
    }
    # Run using the installed interpreter in a non-source CWD. Validate closure as well as imports.
    code = """import importlib.metadata as m, json, re, sys
expected, pins, role = json.loads(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
normalize = lambda s: re.sub(r"[-_.]+", "-", s).lower()
installed = {normalize(d.metadata["Name"]): d.version for d in m.distributions()}
assert all(installed.get(n) == v for n, v in expected.items())
assert all(n in expected or pins.get(n) == v for n, v in installed.items()), "unlocked dependency"
from packaging.requirements import Requirement
distributions = {normalize(d.metadata["Name"]): d for d in m.distributions()}
# Check base requirements for every installed package, but enable optional
# requirements only for the selected role and explicit extras on dependency edges.
pending = [(name, "") for name in distributions]
pending += [("ai-device", extra) for extra in json.loads(sys.argv[4])]
visited = set()
while pending:
    name, extra = pending.pop()
    if (name, extra) in visited:
        continue
    visited.add((name, extra))
    for raw in distributions[name].requires or []:
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate({"extra": extra}):
            continue
        dependency = normalize(requirement.name)
        version = installed.get(dependency)
        assert version is not None and version in requirement.specifier, "broken dependency"
        pending += [(dependency, enabled) for enabled in {"", *requirement.extras}]
if role == "client":
    from device_runtime.executor import LocalExecutor
    from mcp_server_bash.core import ExecutionRuntime
    from mcp_server_fs.core import read_image
    import PIL
    required_map_format = json.loads(sys.argv[5])
    if required_map_format is not None:
        from device_runtime.tasks import SUPPORTED_OUTPUT_MAP_FORMATS
        assert any(type(v) is int and v == required_map_format
                   for v in SUPPORTED_OUTPUT_MAP_FORMATS), "required task output map unsupported"
else:
    assert "ai-device-bash" not in installed and "ai-device-fs" not in installed
    if role == "center":
        from device_runtime.server import create_app
        import uvicorn
        assert callable(uvicorn.run)
    else:
        from device_runtime.mcp_server import create_mcp
"""
    _invoke(
        [
            str(python),
            "-I",
            "-B",
            "-c",
            code,
            json.dumps(expected),
            json.dumps(manifest["constraints"]["pins"]),
            role,
            json.dumps(ROLES[role]["device_extras"]),
            json.dumps(required_output_map_format),
        ],
        cwd=cwd,
        log=log,
    )


def _supported_host():
    if sys.platform not in ("darwin", "linux"):
        raise InstallationError("delivery supports macOS and Linux clients only")


def _check_target(manifest, roles):
    _supported_host()
    if manifest["target"]["system"] != sys.platform:
        raise InstallationError("bundle target does not match host platform")
    if manifest["format_version"] == 3:
        libc, version = platform.libc_ver()
        try:
            glibc = tuple(int(part) for part in version.split(".")[:2])
        except ValueError:
            glibc = ()
        if platform.machine() != "x86_64" or libc != "glibc" or glibc < (2, 17):
            raise InstallationError("local dependencies require x86_64 Linux glibc >= 2.17")
    if not roles or any(role not in manifest["roles"] for role in roles):
        raise InstallationError("installed roles are not supported by this bundle target")


def _prepare(root, bundle, manifest, roles, *, required_output_map_format=None):
    _check_target(manifest, roles)
    if required_output_map_format is None:
        required_output_map_format = _required_output_map_format(root)
    uv = shutil.which("uv")
    if not uv:
        raise InstallationError("uv must be installed and available on PATH")
    release = manifest["release_version"] + "-" + uuid.uuid4().hex
    directory = root / "releases" / release
    directory.mkdir(parents=True)
    snapshot = directory / "bundle"
    shutil.copytree(bundle, snapshot)
    if _fingerprint(inspect_bundle(snapshot)) != _fingerprint(manifest):
        raise InstallationError("bundle changed during preparation")
    _client_contract(snapshot, manifest)
    for role in roles:
        home = directory / role
        home.mkdir()
        log = home / "prepare.log"
        venv = home / "venv"
        _invoke([uv, "--no-config", "venv", "--python", "3.13", str(venv)], cwd=home, log=log)
        python = venv / "bin" / "python"
        wheels = []
        for component in ROLES[role]["components"]:
            entry = manifest["components"][component]
            name = entry["distribution"]
            if component == "device":
                name += "[" + ",".join(ROLES[role]["device_extras"]) + "]"
            # A PEP 508 file URI preserves spaces and special characters in instance roots.
            wheels.append(f"{name} @ {(snapshot / entry['path']).as_uri()}")
        dependency_args = []
        if manifest["format_version"] == 3:
            dependency_args = ["--no-index"]
            dependency_args.extend(
                str(snapshot / "dependencies" / entry["path"])
                for entry in manifest["dependencies"]["manifest"]["wheels"]
            )
        _invoke(
            [
                uv,
                "--no-config",
                "pip",
                "install",
                "--python",
                str(python),
                "--only-binary",
                ":all:",
                "--constraints",
                "../bundle/constraints.txt",
                *wheels,
                *dependency_args,
            ],
            cwd=home,
            log=log,
        )
        _invoke([uv, "--no-config", "pip", "check", "--python", str(python)], cwd=home, log=log)
        _verify_environment(
            python,
            role,
            manifest,
            cwd=home,
            log=log,
            required_output_map_format=required_output_map_format,
        )
    active = {
        "format_version": 1,
        "release": release,
        "roles": roles,
        "release_version": manifest["release_version"],
        "bundle_sha256": _fingerprint(manifest),
    }
    _json(directory / "installation.json", active)
    return active


@with_dependencies
def install_instance(bundle, *, instance_id, roles, root=None, server_url=None, port=8765):
    _supported_host()
    instance_id = _identity(instance_id)
    roles = sorted(set(roles))
    if not roles or any(r not in ROLES for r in roles):
        raise InstallationError("select one or more center/mcp/client roles")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise InstallationError("port must be between 1 and 65535")
    if any(r in roles for r in ("mcp", "client")):
        from .client import device_url

        # Reuse the runtime's loopback/TLS policy before creating any instance directory.
        parts = urlsplit(server_url or "")
        if parts.scheme not in ("http", "https"):
            raise InstallationError("server URL must be an HTTP(S) center root")
        websocket_url = parts._replace(scheme="wss" if parts.scheme == "https" else "ws").geturl()
        device_url(websocket_url, instance_id)
    bundle = Path(bundle).resolve()
    manifest = inspect_bundle(bundle)
    _check_target(manifest, roles)
    _client_contract(bundle, manifest)
    root = (
        Path(root).expanduser().absolute()
        if root
        else (Path.home() / ".device-runtime" / "instances" / instance_id)
    )
    try:
        root.mkdir(parents=True, exist_ok=False)
        with _lock(root):
            active = _prepare(root, bundle, manifest, roles)
            (root / "config").mkdir()
            for role in roles:
                (root / "state" / role).mkdir(parents=True)
            config = {
                r: (
                    {"port": port}
                    if r == "center"
                    else {
                        "server_url": websocket_url if r == "client" else server_url,
                        "device_id": instance_id,
                    }
                )
                for r in roles
            }
            _json(root / "config" / "roles.json", config)
            _json(root / "instance.json", {"format_version": 1, "instance_id": instance_id})
            runner = root / "run"
            runner.write_text(RUNNER, encoding="utf-8")
            runner.chmod(0o755)
            _json(root / "active.json", active)
        return {"instance_root": str(root), **active}
    except OSError as exc:
        raise InstallationError(
            "cannot create instance; destination must not already exist"
        ) from exc


@with_dependencies
def rebuild_instance(root, *, bundle=None):
    _supported_host()
    root = Path(root).expanduser().resolve()
    try:
        # Do not create files in an unrelated directory.
        identity = json.loads((root / "instance.json").read_text())
        if identity["format_version"] != 1:
            raise InstallationError("unsupported instance format")
        _identity(identity["instance_id"])
        with _lock(root):
            active = json.loads((root / "active.json").read_text())
            release = active["release"]
            if not isinstance(release, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.+!-]*", release
            ):
                raise InstallationError("invalid release pointer")
            if not (root / "config" / "roles.json").is_file():
                raise InstallationError("persistent configuration is missing; use backup recovery")
            roles = active["roles"]
            if (
                not roles
                or any(role not in ROLES for role in roles)
                or len(set(roles)) != len(roles)
            ):
                raise InstallationError("invalid installed roles")
            if any(not (root / "state" / role).is_dir() for role in roles):
                raise InstallationError("persistent state is missing; rebuilding is not recovery")
            _required_output_map_format(root)
            source = Path(bundle).resolve() if bundle else root / "releases" / release / "bundle"
            manifest = inspect_bundle(source)
            if _fingerprint(manifest) != active["bundle_sha256"]:
                raise InstallationError("rebuild requires the identical bundle, not an upgrade")
            candidate = _prepare(root, source, manifest, roles)
            if "task_output_map_sha256" in active:
                candidate = {
                    **candidate,
                    "task_output_map_sha256": active["task_output_map_sha256"],
                }
            # Last operation: no writes to config/state/identity/runner, no process starts.
            _json(root / "active.json", candidate)
            return {"instance_root": str(root), **candidate}
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "instance metadata is missing or invalid; active release unchanged"
        ) from exc


def add_instance_parser(modes):
    parser = modes.add_parser("instance", help="Manage a macOS instance or Linux client instance")
    actions = parser.add_subparsers(dest="instance_action", required=True)
    install = actions.add_parser(
        "install", help="Create a new instance; never overwrite existing data"
    )
    install.add_argument("--bundle", type=Path, required=True)
    install.add_argument("--dependency-dir", type=Path)
    install.add_argument("--instance-id", required=True)
    install.add_argument("--root", type=Path)
    install.add_argument("--role", choices=tuple(ROLES), action="append", required=True)
    install.add_argument("--server-url")
    install.add_argument("--port", type=int, default=8765)
    rebuild = actions.add_parser(
        "rebuild", help="Prepare same-bundle environments; instance must be stopped"
    )
    rebuild.add_argument("--root", type=Path, required=True)
    rebuild.add_argument("--bundle", type=Path)
    rebuild.add_argument("--dependency-dir", type=Path)
    from .lifecycle import add_lifecycle_parsers

    add_lifecycle_parsers(actions)


def run_instance(args):
    if args.instance_action == "install":
        result = install_instance(
            args.bundle,
            instance_id=args.instance_id,
            roles=args.role,
            root=args.root,
            server_url=args.server_url,
            port=args.port,
            dependency_dir=getattr(args, "dependency_dir", None),
        )
    elif args.instance_action == "rebuild":
        result = rebuild_instance(
            args.root, bundle=args.bundle, dependency_dir=getattr(args, "dependency_dir", None)
        )
    else:
        from .lifecycle import run_lifecycle

        result = run_lifecycle(args)
    print(json.dumps(result, ensure_ascii=False))
