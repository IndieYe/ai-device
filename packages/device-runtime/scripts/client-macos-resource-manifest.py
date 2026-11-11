"""Offline resource inventory/verification. Never installs or executes resources.

This Python build-time tool is not a target-machine preflight. The eventual
native installer must verify before publication without requiring old Python.
Assumes an immutable, trusted local build input during each scan. Not an ACL,
concurrent-writer, signing or hostile-local-code security boundary.
"""

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

MAX_ENTRIES = 50000
MAX_BYTES = 2_000_000_000
MAX_FILE_BYTES = 250_000_000


def inventory(root):
    root = Path(root)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("resource root must be a real directory")
    entries = []
    total = 0

    def walk(directory):
        nonlocal total
        with os.scandir(directory) as stream:
            children = sorted(stream, key=lambda item: item.name)
        for child in children:
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            if any(ord(c) < 32 or ord(c) == 127 for c in relative):
                raise ValueError("resource name contains control characters")
            if len(entries) >= MAX_ENTRIES:
                raise ValueError("resource entry limit exceeded")
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            record = {"path": relative}
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if not target or target.startswith("/") or any(ord(c) < 32 for c in target):
                    raise ValueError("resource symlink target invalid")
                try:
                    resolved = path.resolve(strict=True)
                    resolved.relative_to(root.resolve())
                except (OSError, RuntimeError, ValueError):
                    raise ValueError("resource symlink escapes tree or is broken") from None
                record.update(kind="symlink", target=target)
            elif stat.S_ISDIR(info.st_mode):
                if mode & 0o7022:
                    raise ValueError("resource directory has unsupported permissions")
                record.update(kind="directory", mode=mode)
            elif stat.S_ISREG(info.st_mode):
                if mode & 0o7022 or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
                    raise ValueError("resource file metadata invalid")
                total += info.st_size
                if total > MAX_BYTES:
                    raise ValueError("resource byte limit exceeded")
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as source:
                    actual = os.fstat(source.fileno())
                    if (actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns) != (
                        info.st_dev,
                        info.st_ino,
                        info.st_size,
                        info.st_mtime_ns,
                    ):
                        raise ValueError("resource changed before hashing")
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                    after = os.fstat(source.fileno())
                    if (actual.st_size, actual.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError("resource changed while hashing")
                record.update(kind="file", mode=mode, size=info.st_size, sha256=digest)
            else:
                raise ValueError("resource contains special file")
            entries.append(record)
            if record["kind"] == "directory":
                walk(path)

    walk(root)
    return {"schema_version": 1, "entries": entries, "total_file_bytes": total}


def verify(root, manifest):
    # Compare exact generated shape, so unknown/duplicate/missing records and
    # extra filesystem entries cannot be silently ignored by this offline tool.
    if inventory(root) != manifest:
        raise ValueError("resource inventory mismatch")


def observe_publication(root, target_manifest, *, prior_manifest=None):
    """Read-only offline evidence, never permission to start/replace/rollback.

    A lost writer response is not automatically retried. Re-observe actual
    public tree: target_exact proves bytes only, NOT a successful service start.
    Keep unknown evidence intact for explicit repair. No persistent marker.
    """
    try:
        actual = inventory(root)
    except FileNotFoundError:
        # A broken symlink or missing entry inside a tree is unknown, not absent.
        try:
            Path(root).lstat()
        except FileNotFoundError:
            return {"tree": "absent", "ready": "not_checked"}
        return {"tree": "unknown", "ready": "not_checked"}
    except (OSError, ValueError):
        return {"tree": "unknown", "ready": "not_checked"}
    if actual == target_manifest:
        state = "target_exact"
    elif prior_manifest is not None and actual == prior_manifest:
        state = "prior_exact"
    else:
        state = "unknown"
    return {"tree": state, "ready": "not_checked"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("create", "verify"))
    parser.add_argument("root", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.operation == "create":
        result = inventory(args.root)
        with args.manifest.open("x") as output:
            json.dump(result, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write("\n")
    else:
        verify(args.root, json.loads(args.manifest.read_text()))


if __name__ == "__main__":
    main()
