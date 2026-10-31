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
import tempfile
from pathlib import Path

from . import native_helper as helper


class BootstrapError(ValueError):
    pass


# This versioned bootstrap is installed separately from candidate snapshots.
# Its package provenance and lifecycle still require independent verification.
BOOTSTRAP_ROOT = Path("/opt/ai-device/bootstrap")
# Fallback for installs that predate the pointer, and for tests. A package upgrade
# writes a new versioned directory and moves BOOTSTRAP_POINTER; hard-coding one
# path made every upgrade require editing this module.
TRUSTED_HELPER_BOOTSTRAP = BOOTSTRAP_ROOT / "0.2.2-native1" / "runtime"
BOOTSTRAP_POINTER = BOOTSTRAP_ROOT / "current"


def trusted_helper_bootstrap():
    """Resolve the bootstrap the helper should run from.

    The pointer names a versioned directory, never a path, so a malformed or
    hostile value cannot escape BOOTSTRAP_ROOT. It is read as root-owned
    evidence: the same ancestry check the caller applies to the bootstrap itself
    runs here first, so the pointer cannot become a way around that check.
    """
    try:
        info = BOOTSTRAP_POINTER.lstat()
    except FileNotFoundError:
        return TRUSTED_HELPER_BOOTSTRAP
    if not stat.S_ISREG(info.st_mode):
        raise BootstrapError("bootstrap pointer must be a regular file")
    name = BOOTSTRAP_POINTER.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise BootstrapError("bootstrap pointer names an invalid directory")
    resolved = BOOTSTRAP_ROOT / name / "runtime"
    helper._trusted(BOOTSTRAP_POINTER)
    helper._trusted(resolved, directory=False)
    return resolved


def current_name():
    """Return the name the pointer holds, without requiring the tree to exist.

    An upgrade deletes the directory the pointer names, so resolving the
    bootstrap after ``dpkg`` runs fails by design. Recording which version was
    in use before the package changed therefore has to read the pointer itself.
    """
    try:
        info = BOOTSTRAP_POINTER.lstat()
    except FileNotFoundError:
        return TRUSTED_HELPER_BOOTSTRAP.parent.name
    if not stat.S_ISREG(info.st_mode):
        raise BootstrapError("bootstrap pointer must be a regular file")
    helper._trusted(BOOTSTRAP_POINTER)
    name = BOOTSTRAP_POINTER.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise BootstrapError("bootstrap pointer names an invalid directory")
    return name


def publish_pointer(name):
    """Point the helper entrypoint at ``name`` after verifying that tree.

    A package upgrade replaces the versioned directory; the pointer is the only
    record of which one the entrypoints run. It is written atomically and its
    parent directory is fsynced, because a crash between the write and the
    directory update would restore the previous name on many filesystems.

    The caller must already hold the package transaction lease. This validates
    only that the target is a runnable, trusted bootstrap -- it does not decide
    whether switching is authorised.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise BootstrapError("bootstrap pointer names an invalid directory")
    directory = BOOTSTRAP_ROOT / name
    if directory.is_symlink() or not directory.is_dir():
        raise BootstrapError("bootstrap pointer target is not an installed directory")
    runtime = directory / "runtime"
    if not (runtime / "bin" / "python3.13").is_file():
        raise BootstrapError("bootstrap pointer target has no runtime")
    helper._trusted(runtime, directory=False)
    try:
        info = BOOTSTRAP_POINTER.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and not stat.S_ISREG(info.st_mode):
        raise BootstrapError("bootstrap pointer must be a regular file")
    descriptor, tmp = tempfile.mkstemp(prefix=".current-", dir=BOOTSTRAP_ROOT)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(name + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, BOOTSTRAP_POINTER)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    fd = os.open(BOOTSTRAP_ROOT, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return name


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
    if type(receipt.get("schema_version")) is not int or receipt.get("schema_version") != 1 or receipt.get("state") != "runtime_snapshot":
        raise BootstrapError("invalid runtime snapshot receipt")
    if not helper._same_json(_inventory(runtime), receipt["inventory"]):
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
        if not helper._same_json(json.loads(path.read_bytes()), receipt):
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
    if not stat.S_ISREG(path.lstat().st_mode):
        raise BootstrapError("candidate evidence must be a regular file")
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
    if not helper._same_json(value, evidence):
        raise BootstrapError("candidate publication evidence differs")
    return evidence








































