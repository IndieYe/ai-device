"""Build and inspect platform-specific delivery artifacts; never run services."""

import copy
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.version import InvalidVersion, Version

from .dependencies import inspect_dependencies

COMPONENTS = {
    "device": ("ai-device", ("device_runtime/cli.py",)),
    "bash": ("ai-device-bash", ("mcp_server_bash/core.py",)),
    "fs": (
        "ai-device-fs",
        (
            "mcp_server_fs/core/__init__.py",
            "mcp_server_fs/core/read_file.py",
            "mcp_server_fs/core/write_file.py",
            "mcp_server_fs/core/edit_file.py",
            "mcp_server_fs/core/insert_file.py",
            "mcp_server_fs/core/read_image.py",
        ),
    ),
}
TARGET = {"system": "darwin", "requires_python": ">=3.13", "offline": False}
ROLES = {
    "center": {"components": ["device"], "device_extras": ["server"]},
    "mcp": {"components": ["device"], "device_extras": ["mcp"]},
    "client": {"components": ["device", "bash", "fs"], "device_extras": ["client"]},
}


class BundleError(ValueError):
    """Invalid or incomplete delivery artifacts, not a runtime-service failure."""


def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.!+_-]{0,63}", value):
        raise BundleError("version must be a non-empty explicit version string")
    try:
        Version(value)
    except InvalidVersion as exc:
        raise BundleError("version must be a valid PEP 440 version") from exc
    return value


def _sources(value):
    if not isinstance(value, dict) or set(value) != set(COMPONENTS):
        raise BundleError("sources must contain exactly device, bash and fs commits")
    if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{40}", v) for v in value.values()):
        raise BundleError("sources must use full lowercase 40-character Git commit IDs")
    return value


def _constraints(path):
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)", line)
        if not match:
            raise BundleError("constraints must contain exact name==version pins only")
        name, version = _normalize(match[1]), match[2]
        if name in pins or name in {item[0] for item in COMPONENTS.values()}:
            raise BundleError("constraints contain duplicate or bundled distribution names")
        pins[name] = _version(version)
    if not pins:
        raise BundleError("third-party constraints cannot be empty")
    return pins


def _wheel(path, component):
    expected, required = COMPONENTS[component]
    if path.suffix != ".whl":
        raise BundleError(f"{component}: expected an explicit wheel file")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                raise BundleError(f"{component}: wheel must contain one distribution metadata file")
            message = BytesParser().parsebytes(archive.read(metadata[0]))
            name, version = message.get("Name", ""), message.get("Version", "")
            if _normalize(name) != expected:
                raise BundleError(f"{component}: wrong distribution; expected {expected}")
            _version(version)
            filename_parts = path.name[:-4].split("-")
            if (
                len(filename_parts) != 5
                or _normalize(filename_parts[0]) != expected
                or filename_parts[1] != version
                or filename_parts[2:] != ["py3", "none", "any"]
            ):
                raise BundleError(f"{component}: expected matching py3-none-any wheel filename")
            info_directory = metadata[0].removesuffix("/METADATA")
            info_parts = info_directory.removesuffix(".dist-info").rsplit("-", 1)
            if (
                "/" in info_directory
                or len(info_parts) != 2
                or _normalize(info_parts[0]) != expected
                or info_parts[1] != version
            ):
                raise BundleError(f"{component}: dist-info directory identity/version mismatch")
            if any(entry not in names for entry in required):
                raise BundleError(f"{component}: required runtime/core entry missing")
            wheel_metadata = archive.read(metadata[0].removesuffix("METADATA") + "WHEEL")
            wheel_message = BytesParser().parsebytes(wheel_metadata)
            if wheel_message.get("Root-Is-Purelib", "").lower() != "true" or wheel_message.get_all(
                "Tag"
            ) != ["py3-none-any"]:
                raise BundleError(f"{component}: expected pure Python py3-none-any wheel")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise BundleError(f"{component}: incomplete or invalid wheel") from exc
    return {"distribution": expected, "version": version, "path": "wheels/" + path.name}


def target_contract(system):
    """Linux delivery currently supports the client only; macOS keeps all three roles."""
    if system not in ("darwin", "linux"):
        raise BundleError("unsupported bundle target system")
    target = {**TARGET, "system": system}
    roles = ROLES if system == "darwin" else {"client": ROLES["client"]}
    return target, copy.deepcopy(roles)


def inspect_bundle(directory):
    """Verify copied artifact bytes and identity; does not prove installation or core behavior."""
    directory = Path(directory)
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        target, roles = target_contract(manifest["target"]["system"])
        if (
            manifest["format_version"] not in (2, 3)
            or manifest["target"] != target
            or manifest["roles"] != roles
        ):
            raise BundleError("unsupported bundle format, target or role layout")
        _version(manifest["release_version"])
        entries = manifest["components"]
        if not isinstance(entries, dict) or set(entries) != set(COMPONENTS):
            raise BundleError("bundle must contain exactly device, bash and fs")
        _sources({component: entry["source_commit"] for component, entry in entries.items()})
        expected_files = {"manifest.json", "constraints.txt"}
        for component, entry in entries.items():
            relative = entry["path"]
            if (
                not isinstance(relative, str)
                or not relative.startswith("wheels/")
                or relative != "wheels/" + Path(relative).name
            ):
                raise BundleError("wheel path must be a direct child of wheels/")
            path = directory / relative
            if _sha256(path) != entry["sha256"]:
                raise BundleError(f"{component}: wheel hash mismatch")
            if any(entry.get(key) != value for key, value in _wheel(path, component).items()):
                raise BundleError(f"{component}: wheel metadata does not match manifest")
            expected_files.add(relative)
        constraints = manifest["constraints"]
        if constraints["path"] != "constraints.txt":
            raise BundleError("constraints path must be constraints.txt")
        path = directory / "constraints.txt"
        if _sha256(path) != constraints["sha256"]:
            raise BundleError("constraints hash mismatch")
        if _constraints(path) != constraints["pins"]:
            raise BundleError("constraints pins do not match manifest")
        if manifest["format_version"] == 3:
            if target["system"] != "linux" or manifest["dependencies"]["path"] != "dependencies":
                raise BundleError("local dependency bundles require the Linux client target")
            try:
                dependencies = inspect_dependencies(directory / "dependencies", constraints["pins"])
            except ValueError as exc:
                raise BundleError(str(exc)) from exc
            if dependencies != manifest["dependencies"]["manifest"]:
                raise BundleError("dependency manifest mismatch")
            expected_files.add("dependencies/manifest.json")
            expected_files.update(
                "dependencies/" + entry["path"] for entry in dependencies["wheels"]
            )
        elif "dependencies" in manifest:
            raise BundleError("dependencies require bundle format 3")
        actual_files = {
            str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file()
        }
        if actual_files != expected_files:
            raise BundleError("bundle contains missing or unlisted files")
        return manifest
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        raise BundleError("bundle is incomplete or has an invalid manifest") from exc


def build_bundle(
    output,
    *,
    release_version,
    wheels,
    constraints,
    sources,
    target_system="darwin",
    dependency_dir=None,
):
    """Stage complete artifacts before publishing; an existing destination is never replaced."""
    target, roles = target_contract(target_system)
    output = Path(output).absolute()
    release_version, sources = _version(release_version), _sources(sources)
    if not isinstance(wheels, dict) or set(wheels) != set(COMPONENTS):
        raise BundleError("provide exactly device, bash and fs wheels")
    if output.exists() or output.is_symlink():
        raise BundleError("output already exists; choose a new bundle directory")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".device-bundle-", dir=output.parent) as temporary:
            stage = Path(temporary) / "bundle"
            (stage / "wheels").mkdir(parents=True)
            entries = {}
            for component, source in wheels.items():
                source = Path(source)
                destination = stage / "wheels" / source.name
                if destination.exists():
                    raise BundleError("component wheel filenames must be distinct")
                shutil.copyfile(source, destination)
                entries[component] = {
                    **_wheel(destination, component),
                    "sha256": _sha256(destination),
                    "source_commit": sources[component],
                }
            shutil.copyfile(constraints, stage / "constraints.txt")
            manifest = {
                "format_version": 2,
                "release_version": release_version,
                "target": target,
                "roles": roles,
                "components": entries,
                "constraints": {
                    "path": "constraints.txt",
                    "sha256": _sha256(stage / "constraints.txt"),
                    "pins": _constraints(stage / "constraints.txt"),
                },
            }
            if dependency_dir is not None:
                if target_system != "linux":
                    raise BundleError("local dependency bundles require Linux")
                try:
                    dependencies = inspect_dependencies(
                        dependency_dir, manifest["constraints"]["pins"]
                    )
                except ValueError as exc:
                    raise BundleError(str(exc)) from exc
                shutil.copytree(dependency_dir, stage / "dependencies")
                manifest["format_version"] = 3
                manifest["dependencies"] = {"path": "dependencies", "manifest": dependencies}
            (stage / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            inspect_bundle(stage)
            # Reserve exclusively, then publish by same-filesystem rename. No partial files
            # become visible, and another builder cannot reserve the same destination.
            output.mkdir()
            try:
                stage.replace(output)
            except BaseException:
                output.rmdir()  # Only the empty directory reserved by this build.
                raise
    except OSError as exc:
        raise BundleError("cannot read artifacts or publish the new bundle directory") from exc
    return manifest


def with_dependencies(function):
    """Snapshot an explicitly supplied wheel set before any lifecycle mutation."""
    from functools import wraps
    from inspect import signature

    @wraps(function)
    def wrapped(*args, dependency_dir=None, **kwargs):
        if dependency_dir is None:
            return function(*args, **kwargs)
        bound = signature(function).bind(*args, **kwargs)
        bundle = bound.arguments.get("bundle")
        if bundle is None:
            raise BundleError("--dependency-dir requires an explicit --bundle")
        manifest = inspect_bundle(bundle)
        if manifest["format_version"] != 2:
            raise BundleError("dependency override requires a legacy v2 bundle; v3 is immutable")
        with tempfile.TemporaryDirectory(prefix="device-local-dependencies-") as directory:
            output = Path(directory) / "bundle"
            build_bundle(
                output,
                release_version=manifest["release_version"],
                wheels={c: Path(bundle) / e["path"] for c, e in manifest["components"].items()},
                constraints=Path(bundle) / "constraints.txt",
                sources={c: e["source_commit"] for c, e in manifest["components"].items()},
                target_system=manifest["target"]["system"],
                dependency_dir=dependency_dir,
            )
            bound.arguments["bundle"] = output
            return function(*bound.args, **bound.kwargs)

    return wrapped


def add_bundle_parser(modes):
    parser = modes.add_parser("bundle", help="Build or inspect a delivery bundle; no installation")
    actions = parser.add_subparsers(dest="bundle_action", required=True)
    build = actions.add_parser("build")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--version", required=True)
    build.add_argument("--target-system", choices=("darwin", "linux"), default="darwin")
    for component in COMPONENTS:
        build.add_argument(f"--{component}-wheel", type=Path, required=True)
    build.add_argument("--constraints", type=Path, required=True)
    build.add_argument("--dependency-dir", type=Path, help="Verified complete local wheel set")
    build.add_argument(
        "--sources", type=Path, required=True, help="JSON: device/bash/fs full commits"
    )
    inspect = actions.add_parser("inspect")
    inspect.add_argument("directory", type=Path)


def run_bundle(args):
    if args.bundle_action == "inspect":
        manifest = inspect_bundle(args.directory)
    else:
        try:
            sources = json.loads(args.sources.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BundleError("cannot read sources JSON") from exc
        manifest = build_bundle(
            args.output,
            release_version=args.version,
            wheels={component: getattr(args, component + "_wheel") for component in COMPONENTS},
            constraints=args.constraints,
            sources=sources,
            target_system=args.target_system,
            dependency_dir=args.dependency_dir,
        )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
