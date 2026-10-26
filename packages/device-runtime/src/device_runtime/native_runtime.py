"""Offline native-runtime preparation. No downloads, services, or active pointer writes.

Run from the installer/bootstrap, never directly on an async request path.
"""

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


class RuntimePreparationError(ValueError):
    """The candidate was not published; existing destinations are never overwritten."""


def load_lock(path):
    try:
        lock = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            type(lock["schema_version"]) is not int
            or lock["schema_version"] != 1
            or lock["implementation"] != "cpython"
        ):
            raise ValueError("unsupported runtime lock")
        if not re.fullmatch(r"3\.13\.\d+", lock["python_version"]):
            raise ValueError("expected an exact CPython 3.13 version")
        if not re.fullmatch(r"[0-9]{8}", lock["upstream_release"]):
            raise ValueError("exact upstream release required")
        if not lock["license_url"].startswith("https://"):
            raise ValueError("runtime license provenance required")
        for target, entry in lock["targets"].items():
            if target not in {"linux-x86_64", "darwin-arm64"}:
                raise ValueError("unsupported native target")
            if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise ValueError("invalid runtime SHA256")
            if type(entry["size"]) is not int or entry["size"] <= 0:
                raise ValueError("invalid runtime size")
            if not entry["url"].startswith(
                "https://github.com/astral-sh/python-build-standalone/releases/download/"
                + lock["upstream_release"]
                + "/"
            ):
                raise ValueError("unrecognized runtime source")
        if not lock["targets"]:
            raise ValueError("runtime targets required")
        return lock
    except (OSError, KeyError, TypeError, AttributeError, ValueError) as exc:
        raise RuntimePreparationError(f"invalid runtime lock: {exc}") from exc


def _host_target():
    machine = {"aarch64": "arm64", "AMD64": "x86_64"}.get(platform.machine(), platform.machine())
    return f"{platform.system().lower()}-{machine}"


def _probe(python, version):
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    env["PATH"] = "/usr/bin:/bin"
    result = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            (
                "import json,platform,ssl,sqlite3,ctypes; "
                "print(json.dumps({'version':platform.python_version(),"
                "'implementation':platform.python_implementation()}))"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    report = json.loads(result.stdout)
    if report != {"version": version, "implementation": "CPython"}:
        raise RuntimePreparationError("runtime executable does not match locked CPython")
    return report


def prepare_runtime(archive, destination, *, lock_path, target):
    """Verify a local locked archive, extract and probe before atomic publication.

    The destination is a new immutable runtime, not an installation or venv.
    Two probes around a directory rename verify interpreter relocatability.
    """
    lock = load_lock(lock_path)
    if target != _host_target() or target not in lock["targets"]:
        raise RuntimePreparationError("runtime target mismatch with this host")
    archive, destination = Path(archive), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise RuntimePreparationError("runtime destination already exists")
    entry = lock["targets"][target]
    if archive.stat().st_size != entry["size"]:
        raise RuntimePreparationError("runtime archive size mismatch")
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
            raise RuntimePreparationError("runtime archive SHA256 mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".native-runtime-", dir=destination.parent))
    try:
        unpacked = stage / "unpacked"
        unpacked.mkdir()
        with tarfile.open(archive, "r:gz") as source:
            members = source.getmembers()
            names = set()
            total = 0
            for member in members:
                name = member.name.rstrip("/")
                parts = name.split("/")
                if (
                    not name
                    or name.startswith("/")
                    or ".." in parts
                    or parts[0] != "python"
                    or name in names
                    or not (member.isfile() or member.isdir() or member.issym())
                    or member.mode & 0o6000
                ):
                    raise RuntimePreparationError("unsupported runtime archive member")
                names.add(name)
                total += member.size
                if len(names) > 100000 or total > 2_000_000_000:
                    raise RuntimePreparationError("runtime archive exceeds extraction budget")
            # Python's data filter also refuses escaping links and writes via links.
            source.extractall(unpacked, members=members, filter="data")
        runtime = unpacked / "python"
        python = runtime / "bin/python3.13"
        first = _probe(python, lock["python_version"])
        relocated = stage / "relocated"
        runtime.rename(relocated)
        second = _probe(relocated / "bin/python3.13", lock["python_version"])
        # Serialize publication independently of the destination's absence check.
        with (destination.parent / ".native-runtime.lock").open("a+b") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if destination.exists() or destination.is_symlink():
                raise RuntimePreparationError("runtime destination already exists")
            relocated.rename(destination)
        return {"target": target, "sha256": entry["sha256"], "probes": [first, second]}
    except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as exc:
        if isinstance(exc, RuntimePreparationError):
            raise
        raise RuntimePreparationError(f"runtime preparation failed: {exc}") from exc
    finally:
        shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description="Prepare an offline locked native CPython runtime")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--target", choices=["darwin-arm64", "linux-x86_64"], required=True)
    args = parser.parse_args()
    try:
        result = prepare_runtime(
            args.archive, args.destination, lock_path=args.lock, target=args.target
        )
    except (RuntimePreparationError, OSError) as exc:
        parser.exit(1, f"runtime preparation refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
