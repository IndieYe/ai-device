"""Mac role installation. Prepare immutable environments, then publish one pointer.

This module does not start services, replay operations, or write persistent state.
Failed candidates are retained for inspection and never become active.
"""

import contextvars
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
from .entrypoint import LAUNCHER, POINTER, RUNNER, RUNNER_BODY
from .management_lock import management_lease


class InstallationError(ValueError):
    """An installation failed without changing an active release."""


# Deliberately stdlib-only: rebuilding cannot depend on an intact active venv.


def _write_pointer(root, release):
    """Atomically replace the single-line release pointer.

    The value is validated first so a bug cannot write a path fragment. Callers
    must already hold the installation lock: the pointer and active.json are
    read by different processes and must move together.
    """
    if not isinstance(release, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.+!-]*", release
    ):
        raise InstallationError("invalid release pointer value")
    path = root / POINTER
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(release + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _has_pointer(root):
    """Legacy instances installed without a private runtime have no pointer."""
    path = root / POINTER
    return path.is_file() and not path.is_symlink()


def _read_pointer(root):
    path = root / POINTER
    if path.is_symlink() or not path.is_file():
        raise InstallationError("release pointer is missing or invalid")
    value = path.read_text(encoding="utf-8")
    if value.count("\n") > 1:
        raise InstallationError("release pointer must be a single line")
    return value.rstrip("\n")


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
    with management_lease(root), (root / ".installation.lock").open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InstallationError(
                "instance is running or another installation is active"
            ) from exc
        yield


def _write_candidate_record(root, active, prepared):
    """Write the lifecycle binding that activate_release verifies.

    lifecycle.prepare_upgrade records this itself; the native preparation path
    builds the release through _prepare directly and therefore has to emit the
    same record, or the prepared release can never be promoted.
    """
    _json(
        root / "releases" / prepared["release"] / "candidate.json",
        {
            "format_version": 1,
            "binding": _lifecycle_binding(root, active),
            "candidate": prepared,
        },
    )


def _lifecycle_binding(root, active):
    """Mirror lifecycle._binding without importing it (lifecycle imports us)."""
    return {
        "active": active,
        "identity": json.loads((root / "instance.json").read_text()),
        "identity_sha256": _file_sha256(root / "instance.json"),
        "config_sha256": _file_sha256(root / "config" / "roles.json"),
        "state_format": 1,
    }


def _release_runtime_source(root, release):
    """Private runtime of an existing release, or None for a legacy instance.

    lifecycle._release_runtime returns the same path; it lives here so the native
    preparation path can reuse it without importing lifecycle at module scope.
    """
    if not _has_pointer(root):
        return None
    runtime = root / "releases" / release / "runtime"
    if not runtime.is_dir() or runtime.is_symlink():
        raise InstallationError("active release has no private runtime")
    return runtime


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


_PREPARATION_SENDER = contextvars.ContextVar("native_preparation_sender", default=None)


@contextmanager
def preparation_sender(sender):
    token = _PREPARATION_SENDER.set(sender)
    try:
        yield
    finally:
        _PREPARATION_SENDER.reset(token)


def _invoke(argv, *, cwd, log):
    env = {k: v for k, v in os.environ.items() if not k.startswith("UV_")}
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    # Lifecycle diagnostics pass no log: read-only probes must not create files.
    with log.open("ab") if log is not None else nullcontext(subprocess.DEVNULL) as stream:
        try:
            sender = _PREPARATION_SENDER.get()
            if sender is not None:
                result = sender.run(
                    argv, cwd=cwd, env=env, stdout=stream, timeout=60 if log is None else None
                )
            else:
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


def _role_extras(role, manifest):
    """Extras the bundle ships for this role, falling back to the default contract."""
    declared = manifest.get("roles") if isinstance(manifest, dict) else None
    contract = declared.get(role) if isinstance(declared, dict) else None
    if isinstance(contract, dict) and isinstance(contract.get("device_extras"), list):
        return contract["device_extras"]
    return ROLES[role]["device_extras"]


def _verify_environment(python, role, manifest, *, cwd, log, required_output_map_format=None):
    # The bundle declares what it ships; the module constant only describes the
    # default contract and would disagree with a Linux bundle that adds native.
    extras = _role_extras(role, manifest)
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
            json.dumps(extras),            json.dumps(required_output_map_format),
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
        from .dependencies import dependency_target

        try:
            minimum = dependency_target(manifest["dependencies"]["manifest"]["target"])
        except (KeyError, ValueError, TypeError) as exc:
            raise InstallationError("invalid local dependency target") from exc
        # The wheel set is built for one architecture. Linux additionally has a
        # libc floor; macOS wheels embed their own deployment target, so the
        # version a given wheel supports is already encoded in its platform tag
        # and re-derived on the installed interpreter below.
        if manifest["target"]["system"] == "darwin":
            if platform.machine() != "arm64":
                raise InstallationError("local dependencies require arm64 macOS")
            version = platform.mac_ver()[0]
            try:
                macos = tuple(int(part) for part in version.split(".")[:2])
            except ValueError:
                macos = ()
            if not macos or macos < minimum:
                raise InstallationError(
                    f"local dependencies require macOS >= {minimum[0]}.{minimum[1]}"
                )
        else:
            libc, version = platform.libc_ver()
            try:
                glibc = tuple(int(part) for part in version.split(".")[:2])
            except ValueError:
                glibc = ()
            if platform.machine() != "x86_64" or libc != "glibc" or glibc < minimum:
                raise InstallationError(
                    f"local dependencies require x86_64 Linux glibc >= {minimum[0]}.{minimum[1]}"
                )
    if not roles or any(role not in manifest["roles"] for role in roles):
        raise InstallationError("installed roles are not supported by this bundle target")


def _prepare(
    root,
    bundle,
    manifest,
    roles,
    *,
    required_output_map_format=None,
    uv_executable=None,
    runtime_source=None,
):
    _check_target(manifest, roles)
    if required_output_map_format is None:
        required_output_map_format = _required_output_map_format(root)
    if uv_executable is None:
        uv = shutil.which("uv")
        if not uv:
            raise InstallationError("uv must be installed and available on PATH")
    else:
        tool = Path(uv_executable)
        if not tool.is_absolute() or not tool.is_file() or not os.access(tool, os.X_OK):
            raise InstallationError("explicit uv must be an absolute executable file")
        uv = str(tool.resolve())
    # UV_* is intentionally filtered by _invoke. Do not ask uv to rediscover
    # "3.13": that may select a different, root-private managed interpreter.
    # Resolve the bootstrap venv symlink so releases do not depend on its lifetime.
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13):
        raise InstallationError("run the installer with CPython 3.13 at a durable shared path")
    interpreter = Path(sys.executable).resolve()
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise InstallationError("installer Python must be an executable at a durable shared path")
    release = manifest["release_version"] + "-" + uuid.uuid4().hex
    directory = root / "releases" / release
    directory.mkdir(parents=True)
    if runtime_source is not None:
        # Copy the interpreter into this release so the instance owns everything
        # it needs to start. A package erase or a newer package version then
        # leaves the release intact instead of breaking its shebang. The venv is
        # built from this copy, so venv/bin/python is not a link into the package.
        private_runtime = directory / "runtime"
        shutil.copytree(runtime_source, private_runtime, symlinks=True)
        private_python = private_runtime / "bin" / "python3.13"
        if not private_python.is_file() or not os.access(private_python, os.X_OK):
            raise InstallationError("private runtime has no executable interpreter")
        interpreter = private_python
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
        _invoke(
            [
                uv,
                "--no-config",
                "venv",
                "--no-python-downloads",
                "--python",
                str(interpreter),
                str(venv),
            ],
            cwd=home,
            log=log,
        )
        python = venv / "bin" / "python"
        wheels = []
        extras = _role_extras(role, manifest)
        for component in ROLES[role]["components"]:
            entry = manifest["components"][component]
            name = entry["distribution"]
            if component == "device":
                name += "[" + ",".join(extras) + "]"
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
def install_instance(
    bundle,
    *,
    instance_id,
    roles,
    root=None,
    server_url=None,
    port=8765,
    runner_python=None,
    runtime_source=None,
):
    _supported_host()
    runner_content = RUNNER
    if runtime_source is not None:
        # The instance gets its own copy of the interpreter runtime so a package
        # upgrade or removal cannot pull the interpreter out from under a live
        # service. runner_python is then resolved inside this copy, after the
        # release directory exists, so the shebang never names a package path.
        runtime_source = Path(runtime_source)
        if not runtime_source.is_dir() or runtime_source.is_symlink():
            raise InstallationError("runtime source must be a plain directory")
    if runner_python is not None:
        runner_python = Path(runner_python)
        if (
            not runner_python.is_absolute()
            or any(char.isspace() for char in str(runner_python))
            or len(str(runner_python).encode()) > 120
        ):
            raise InstallationError("runner Python must be an executable absolute shebang path")
        # Without a private runtime the caller must already point at a real
        # executable; with one, the copy is checked once it exists.
        if runtime_source is None and (
            not runner_python.is_file() or not os.access(runner_python, os.X_OK)
        ):
            raise InstallationError(
                "runner Python must be an executable absolute shebang path"
            )
        runner_content = f"#!{runner_python}\n" + RUNNER.split("\n", 1)[1]
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
    except FileExistsError as exc:
        raise InstallationError("cannot create instance; destination already exists") from exc
    except OSError as exc:
        raise InstallationError(f"cannot create instance directory (errno {exc.errno})") from exc
    try:
        with _lock(root):
            active = _prepare(root, bundle, manifest, roles, runtime_source=runtime_source)
            if runtime_source is not None:
                # Point the stable entry at this release's own interpreter, not
                # at the package path, so the instance survives package removal.
                private_python = (
                    root / "releases" / active["release"] / "runtime" / "bin" / "python3.13"
                )
                if not private_python.is_file() or not os.access(private_python, os.X_OK):
                    raise InstallationError("private runtime has no executable interpreter")
                runner_content = f"#!{private_python}\n" + RUNNER.split("\n", 1)[1]
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
            # The stable entry is the shell launcher; the Python body lives in a
            # sibling file so it carries no shebang and cannot be started directly
            # with a stale interpreter.
            runner_body = root / "run-runner"
            runner = root / "run"
            if runtime_source is None:
                # No private runtime to address, so keep the historical single
                # file runner. A launcher here would name an interpreter that
                # does not exist and the instance could never start.
                runner.write_text(runner_content, encoding="utf-8")
                runner.chmod(0o755)
            else:
                # The stable entry is the shell launcher; the Python body lives in
                # a sibling file so it carries no shebang and cannot be started
                # directly with a stale interpreter.
                runner_body.write_text(RUNNER_BODY, encoding="utf-8")
                runner_body.chmod(0o755)
                runner.write_text(LAUNCHER, encoding="utf-8")
                runner.chmod(0o755)
                # Pointer first, metadata second, both under the installation
                # lock. A crash between them leaves the pointer naming a release
                # that active.json does not describe, which RUNNER rejects.
                _write_pointer(root, active["release"])
            _json(root / "active.json", active)
        return {"instance_root": str(root), **active}
    except OSError as exc:
        raise InstallationError(
            f"instance installation failed (errno {exc.errno}); partial instance retained"
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
            # A rebuild must not hand the instance a release it cannot start.
            # Reuse the live release's private runtime so the new environment is
            # self-contained; legacy pointer-less instances keep their entry.
            runtime_source = None
            if _has_pointer(root):
                current_runtime = root / "releases" / active["release"] / "runtime"
                if current_runtime.is_dir() and not current_runtime.is_symlink():
                    runtime_source = current_runtime
                else:
                    raise InstallationError(
                        "active release has no private runtime; rebuild would break the entrypoint"
                    )
            candidate = _prepare(root, source, manifest, roles, runtime_source=runtime_source)
            if "task_output_map_sha256" in active:
                candidate = {
                    **candidate,
                    "task_output_map_sha256": active["task_output_map_sha256"],
                }
            # Last operations: pointer then metadata, inside the same lock. No
            # writes to config/state/identity/runner and no process starts, so a
            # switch only takes effect on the next start by design.
            if runtime_source is not None:
                _write_pointer(root, candidate["release"])
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
    add_launchd_parsers(actions)


def add_launchd_parsers(actions):
    """macOS LaunchAgent deployment; refuses on any other host."""
    parser = actions.add_parser(
        "launchd", help="Install or remove the macOS LaunchAgent for this instance"
    )
    commands = parser.add_subparsers(dest="launchd_action", required=True)
    for name in ("install", "uninstall", "status", "profile"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--label", default=None)
        if name in {"install", "uninstall", "profile"}:
            command.add_argument("--dry-run", action="store_true")
        if name == "install":
            command.add_argument(
                "--path-env",
                default=None,
                help="PATH the agent runs with; defaults to a fixed system path",
            )
            command.add_argument(
                "--env-file",
                type=Path,
                default=None,
                help="env file holding the device token; defaults to launchd/client.env",
            )
        if name == "profile":
            command.add_argument("--allow-upgrade", action="store_true")


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
    elif args.instance_action == "launchd":
        from . import launchd_deploy

        handlers = {
            "install": lambda: launchd_deploy.install(
                args.root,
                label=args.label,
                path_env=args.path_env,
                env_file=args.env_file,
                dry_run=args.dry_run,
            ),
            "uninstall": lambda: launchd_deploy.uninstall(
                args.root, label=args.label, dry_run=args.dry_run
            ),
            "status": lambda: launchd_deploy.status(args.root, label=args.label),
            "profile": lambda: launchd_deploy.write_profile(
                args.root,
                label=args.label,
                allow_upgrade=args.allow_upgrade,
                dry_run=args.dry_run,
            ),
        }
        result = handlers[args.launchd_action]()
    else:
        from .lifecycle import run_lifecycle

        result = run_lifecycle(args)
    print(json.dumps(result, ensure_ascii=False))
