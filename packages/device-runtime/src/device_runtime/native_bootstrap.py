"""Independent transaction runtime snapshots; no package/service execution.

The caller must hold the package transaction lease. Snapshots are deliberately
never cleaned or repaired here. An incomplete directory remains blocked.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

from . import native_helper as helper


class BootstrapError(ValueError):
    pass


# This versioned bootstrap is installed separately from candidate snapshots.
# Its package provenance and lifecycle still require independent verification.
TRUSTED_HELPER_BOOTSTRAP = Path("/opt/ai-device/bootstrap/0.2.2-native1/runtime")


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory(root):
    entries = {}
    total = 0
    for base, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(base) / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if len(entries) >= 50000:
                raise BootstrapError("runtime member budget exceeded")
            if not stat.S_ISLNK(info.st_mode):
                helper._trusted(path, directory=stat.S_ISDIR(info.st_mode))
            else:
                helper._trusted(path.parent, directory=True)
                if info.st_uid != root.stat().st_uid:
                    raise BootstrapError("runtime link owner differs")
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if os.path.isabs(target) or not path.resolve().is_relative_to(root):
                    raise BootstrapError("runtime link must remain inside copied tree")
                if not path.exists():
                    raise BootstrapError("runtime link target unavailable")
                value = {"kind": "symlink", "target": target}
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > 2_000_000_000:
                    raise BootstrapError("runtime byte budget exceeded")
                value = {
                    "kind": "file",
                    "mode": stat.S_IMODE(info.st_mode),
                    "size": info.st_size,
                    "sha256": _digest(path),
                }
            elif stat.S_ISDIR(info.st_mode):
                value = {"kind": "directory", "mode": stat.S_IMODE(info.st_mode)}
            else:
                raise BootstrapError("unsupported runtime member")
            entries[relative] = value
    return entries


def validate_snapshot(directory):
    directory = helper._path(directory)
    helper._trusted(directory, directory=True)
    receipt_path = directory / "snapshot.json"
    helper._trusted(receipt_path)
    receipt = json.loads(receipt_path.read_bytes())
    runtime = directory / "runtime"
    helper._trusted(runtime, directory=True)
    if receipt.get("schema_version") != 1 or receipt.get("state") != "runtime_snapshot":
        raise BootstrapError("invalid runtime snapshot receipt")
    if _inventory(runtime) != receipt["inventory"]:
        raise BootstrapError("runtime snapshot drift")
    python = runtime / "bin/python3.13"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise BootstrapError("snapshot interpreter unavailable")
    return receipt


def snapshot_runtime(source, directory):
    """Copy a trusted CPython tree outside package ownership, never hardlink.

    The destination must be beneath the manager's separately owned transaction
    directory, not within the source runtime. This function does not infer or
    grant package authorization. Reusing a completed snapshot verifies bytes;
    missing receipt never triggers recopy/overwrite.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    source, directory = helper._path(source), helper._path(directory)
    helper._trusted(source, directory=True)
    helper._trusted(directory.parent, directory=True)
    if directory.is_relative_to(source) or source.is_relative_to(directory):
        raise BootstrapError("snapshot must be independent of source tree")
    if directory.exists() or directory.is_symlink():
        receipt = validate_snapshot(directory)
        if receipt["source"] != str(source):
            raise BootstrapError("snapshot source conflict")
        return receipt
    before = _inventory(source)
    python = source / "bin/python3.13"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise BootstrapError("source interpreter unavailable")
    directory.mkdir(mode=0o700)
    with helper._directory_fd(directory.parent) as fd:
        os.fsync(fd)
    copied = directory / "runtime"
    shutil.copytree(source, copied, symlinks=True, copy_function=shutil.copyfile)
    # copyfile does not preserve executable bits; restore exact validated modes.
    for relative, value in before.items():
        if value["kind"] != "symlink":
            (copied / relative).chmod(value["mode"])
    copied.chmod(stat.S_IMODE(source.stat().st_mode))
    if _inventory(source) != before or _inventory(copied) != before:
        raise BootstrapError("runtime changed while copying")
    for relative, value in before.items():
        if value["kind"] == "file":
            with (copied / relative).open("rb") as stream:
                os.fsync(stream.fileno())
    directories = [copied / name for name, value in before.items() if value["kind"] == "directory"]
    for path in sorted(directories, key=lambda p: len(p.parts), reverse=True) + [copied]:
        with helper._directory_fd(path) as fd:
            os.fsync(fd)
    receipt = {
        "schema_version": 1,
        "state": "runtime_snapshot",
        "source": str(source),
        "inventory": before,
    }
    helper._save_new(directory / "snapshot.json", receipt)
    return validate_snapshot(directory)


def _reject_helper_bytecode(snapshot):
    """A source digest cannot identify code loaded from a stale bytecode cache.

    This is only a candidate precondition, not proof of a running process's
    loaded code. Do not delete or rewrite any existing cache automatically.
    """
    inventory = snapshot.get("inventory")
    if not isinstance(inventory, dict):
        raise BootstrapError("invalid runtime inventory")
    if any(
        part == "__pycache__" or part.endswith((".pyc", ".pyo"))
        for relative in inventory
        for part in relative.split("/")
    ):
        raise BootstrapError("helper candidate contains bytecode cache")


def inspect_helper_candidate(directory):
    """Validate an independent snapshot and probe its helper without activating it.

    Only administrator-trusted candidate code is executed. This is not a
    signature check, a service switch, or authorization to replace a bootstrap.
    """
    import subprocess

    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    directory = helper._path(directory)
    snapshot = validate_snapshot(directory)
    _reject_helper_bytecode(snapshot)
    runtime = directory / "runtime"
    python = runtime / "bin/python3.13"
    expected = runtime / "lib/python3.13/site-packages/device_runtime/native_helper.py"
    helper._trusted(expected)
    code = (
        "import json,sys; import device_runtime.native_helper as h; "
        "print(json.dumps({'base':sys.base_prefix,'module':h.__file__,"
        "'version':list(sys.version_info[:2])}))"
    )
    try:
        result = subprocess.run(
            [str(python), "-I", "-B", "-c", code],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
        observed = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise BootstrapError("helper candidate self-check failed") from exc
    if observed != {"base": str(runtime), "module": str(expected), "version": [3, 13]}:
        raise BootstrapError("helper candidate identity differs")
    # Detect modifications during the subprocess before reporting usable evidence.
    validate_snapshot(directory)
    return {
        "state": "helper_candidate_checked_not_activated",
        "snapshot_sha256": _digest(directory / "snapshot.json"),
        "helper_sha256": _digest(expected),
        "python": str(python),
        "activated": False,
    }


def publish_helper_candidate(source, candidates_dir, version):
    """Prepare a versioned, independently checked candidate without activating it.

    The caller must serialize publication with the administrator package lease.
    A partially prepared version is never removed or overwritten.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    helper._identifier(version)
    source = helper._path(source)
    candidates_dir = helper._path(candidates_dir)
    helper._trusted(candidates_dir, directory=True)
    candidate = candidates_dir / version
    # An existing complete snapshot can be revalidated; incomplete data is
    # rejected by snapshot_runtime and retained for explicit review.
    snapshot_runtime(source, candidate)
    checked = inspect_helper_candidate(candidate)
    receipt = {
        "schema_version": 1,
        "state": "helper_candidate_published_not_activated",
        "version": version,
        "source": str(source),
        "snapshot_sha256": checked["snapshot_sha256"],
        "helper_sha256": checked["helper_sha256"],
        "activated": False,
    }
    path = candidate / "candidate.json"
    if os.path.lexists(path):
        helper._trusted(path)
        if json.loads(path.read_bytes()) != receipt:
            raise BootstrapError("candidate publication evidence differs")
    else:
        helper._save_new(path, receipt)
    return receipt


def read_helper_candidate(candidates_dir, version):
    """Verify published candidate evidence without executing or activating code."""
    helper._identifier(version)
    candidates_dir = helper._path(candidates_dir)
    helper._trusted(candidates_dir, directory=True)
    candidate = candidates_dir / version
    snapshot = validate_snapshot(candidate)
    _reject_helper_bytecode(snapshot)
    path = candidate / "candidate.json"
    helper._trusted(path)
    value = json.loads(path.read_bytes())
    expected = candidate / "runtime/lib/python3.13/site-packages/device_runtime/native_helper.py"
    helper._trusted(expected)
    evidence = {
        "schema_version": 1,
        "state": "helper_candidate_published_not_activated",
        "version": version,
        "source": snapshot["source"],
        "snapshot_sha256": _digest(candidate / "snapshot.json"),
        "helper_sha256": _digest(expected),
        "activated": False,
    }
    if value != evidence:
        raise BootstrapError("candidate publication evidence differs")
    return evidence


def run_verified_helper_candidate(candidates_dir, version, *, registry, socket_path, group, expected_snapshot_sha256=None):
    """Execute verified candidate helper bytes in the independent bootstrap.

    This experimental entry point does not provision a unit, prove the caller's
    identity, or authorize dispatch. The caller is trusted administrator code.
    It must run under the separately installed isolated interpreter with bytecode
    writes disabled. Modules are compiled from the exact bytes just checked,
    not imported again from mutable paths.
    """
    import types

    contents = _verified_helper_bytes(candidates_dir, version, expected_snapshot_sha256)
    names = ("__init__", "management_lock", "native_helper")
    # Keep imports for these modules inside this exact validated byte set.
    module_names = ("device_runtime", "device_runtime.management_lock", "device_runtime.native_helper")
    previous = {name: sys.modules.get(name) for name in module_names}
    try:
        for name, qualified in zip(names, module_names, strict=True):
            path, data = contents[name]
            module = types.ModuleType(qualified)
            module.__file__ = str(path)
            module.__package__ = "device_runtime"
            if name == "__init__":
                module.__path__ = [str(path.parent)]
            sys.modules[qualified] = module
            exec(compile(data, str(path), "exec"), module.__dict__)  # noqa: S102
        sys.modules["device_runtime.native_helper"].serve(registry, socket_path, group=group)
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _verified_helper_bytes(candidates_dir, version, expected_snapshot_sha256):
    """Validate the independent bootstrap and return its verified source bytes.

    Internal preparation only; returning bytes does not attest a running process
    or authorize dispatch. Callers must preserve the validated execution boundary.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    candidate = helper._path(candidates_dir) / version
    runtime = candidate / "runtime"
    bootstrap = TRUSTED_HELPER_BOOTSTRAP
    python = bootstrap / "bin/python3.13"
    site = bootstrap / "lib/python3.13/site-packages/device_runtime"
    if (
        Path(sys.executable) != python
        or Path(sys.base_prefix) != bootstrap
        or Path(__file__) != site / "native_bootstrap.py"
        or Path(helper.__file__) != site / "native_helper.py"
        or candidate.is_relative_to(bootstrap)
        or bootstrap.is_relative_to(candidate)
        or not sys.flags.isolated
        or not sys.dont_write_bytecode
    ):
        raise BootstrapError("independent isolated bootstrap required")
    for path in (bootstrap, python, site / "native_bootstrap.py", site / "native_helper.py", site / "management_lock.py"):
        helper._trusted(path, directory=path == bootstrap)
    evidence = read_helper_candidate(candidates_dir, version)
    if expected_snapshot_sha256 is not None and (
        not isinstance(expected_snapshot_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_snapshot_sha256)
        or evidence["snapshot_sha256"] != expected_snapshot_sha256
    ):
        raise BootstrapError("candidate snapshot differs from unit")
    snapshot = validate_snapshot(candidate)
    if _digest(candidate / "snapshot.json") != evidence["snapshot_sha256"]:
        raise BootstrapError("candidate snapshot changed")
    names = ("__init__", "management_lock", "native_helper")
    contents = {}
    for name in names:
        relative = f"lib/python3.13/site-packages/device_runtime/{name}.py"
        entry = snapshot["inventory"].get(relative)
        if not isinstance(entry, dict) or entry.get("kind") != "file":
            raise BootstrapError("candidate helper source missing")
        path = runtime / relative
        helper._trusted(path)
        data = path.read_bytes()
        if len(data) != entry.get("size") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            raise BootstrapError("candidate helper source changed")
        contents[name] = (path, data)
    if hashlib.sha256(contents["native_helper"][1]).hexdigest() != evidence["helper_sha256"]:
        raise BootstrapError("candidate helper evidence differs")
    if validate_snapshot(candidate) != snapshot or _digest(candidate / "snapshot.json") != evidence["snapshot_sha256"]:
        raise BootstrapError("candidate changed during launch")

    return contents


def publish_managed_helper_candidate(registry, source, version):
    """Publish under the fixed manager directory while holding the package lease."""
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    registry = helper._path(registry)
    helper._trusted(registry, directory=True)
    candidates = registry / "bootstrap-candidates"
    # Existing administrator-provisioned directory only; no implicit setup.
    helper._trusted(candidates, directory=True)
    with helper.package_lease(registry, exclusive=True):
        result = publish_helper_candidate(source, candidates, version)
        if read_helper_candidate(candidates, version) != result:
            raise BootstrapError("candidate publication verification differs")
        return result


def inspect_helper_transition(registry, unit_dir, version):
    """Read-only evidence for review, not permission to replace or restart a helper."""
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    registry, unit_dir = map(helper._path, (registry, unit_dir))
    helper._trusted(registry, directory=True)
    helper._trusted(unit_dir, directory=True)
    candidates = registry / "bootstrap-candidates"
    unit = unit_dir / "ai-device-native-helper.service"
    with helper.package_lease(registry, exclusive=True, existing=True):
        helper._trusted(unit)
        before = _digest(unit)
        candidate = read_helper_candidate(candidates, version)
        candidate_path = candidates / version / "candidate.json"
        candidate_digest = _digest(candidate_path)
        # Do not infer process state or protocol compatibility from unit content.
        if _digest(unit) != before:
            raise BootstrapError("helper unit changed during observation")
        return {
            "state": "helper_transition_observed_not_authorized",
            "unit": str(unit),
            "unit_sha256": before,
            "candidate_version": version,
            "candidate_sha256": candidate_digest,
            "snapshot_sha256": candidate["snapshot_sha256"],
            "process_quiescence_verified": False,
            "protocol_compatibility_verified": False,
            "switch_allowed": False,
        }


def main():
    """Explicit experimental candidate daemon entry; no activation or dispatch."""
    import argparse

    parser = argparse.ArgumentParser(description="Run a verified helper candidate (experimental)")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--group", type=int, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--snapshot-sha256")
    args = parser.parse_args()
    try:
        registry = helper._path(args.registry)
        run_verified_helper_candidate(
            registry / "bootstrap-candidates", args.version,
            registry=registry, socket_path=args.socket, group=args.group,
            expected_snapshot_sha256=args.snapshot_sha256,
        )
    except (BootstrapError, helper.HelperError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"candidate helper refused: {exc}\n")


if __name__ == "__main__":
    main()
