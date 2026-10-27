"""Independent transaction runtime snapshots; no package/service execution.

The caller must hold the package transaction lease. Snapshots are deliberately
never cleaned or repaired here. An incomplete directory remains blocked.
"""

import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

from . import native_helper as helper


class BootstrapError(ValueError):
    pass


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
