"""Stage the locked Linux dependency wheel set from native/dependency-lock.json.

Build-time only. Downloads require --download and are restricted to the exact
HTTPS URLs recorded in the lock; every byte is checked against the locked size
and digest. Never installs, executes or repacks a wheel, and never rewrites the
lock. Output is the directory format build_bundle and inspect_dependencies read:
the wheels plus a manifest.json that carries the provenance each of them needs.
"""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def load_lock(path):
    lock = json.loads(Path(path).read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1:
        raise ValueError("unsupported dependency lock schema")
    target = lock.get("target")
    if target != {"system": "linux", "machine": "x86_64", "python": "cp313", "glibc": "2.17"}:
        raise ValueError("unsupported dependency lock target")
    wheels = lock.get("wheels")
    if not isinstance(wheels, list) or not wheels:
        raise ValueError("dependency lock has no wheels")
    seen = set()
    for entry in wheels:
        name = entry.get("path")
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".whl"):
            raise ValueError("locked wheel path must be a plain .whl filename")
        if name in seen:
            raise ValueError("duplicate locked wheel")
        seen.add(name)
        if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64:
            raise ValueError("locked wheel digest must be a sha256 hex string")
        if type(entry.get("size")) is not int or entry["size"] <= 0:
            raise ValueError("locked wheel size must be a positive integer")
        for key in ("source", "license"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f"locked wheel requires {key}")
    if set(lock.get("pins", {})) != {entry["distribution"] for entry in wheels}:
        raise ValueError("locked pins must name exactly the locked distributions")
    # A pin that disagrees with the wheel it is supposed to constrain would let
    # build_bundle accept a set that install cannot reproduce.
    for entry in wheels:
        if lock["pins"][entry["distribution"]] != entry["version"]:
            raise ValueError("locked pin version differs from the locked wheel")
    return lock


def stage(lock, output, *, download=False):
    output = Path(output)
    if output.exists():
        raise ValueError("output already exists; choose a new dependency directory")
    entries = lock["wheels"]
    output.mkdir(parents=True)
    try:
        for entry in entries:
            path = output / entry["path"]
            if path.exists():
                raise ValueError(f"refusing to reuse an existing file: {entry['path']}")
            if not download:
                raise ValueError(f"missing wheel: {entry['path']}; explicit --download required")
            url = entry.get("url")
            if not isinstance(url, str) or urlparse(url).scheme != "https":
                raise ValueError("HTTPS lock URL required")
            with urllib.request.urlopen(url, timeout=60) as response, path.open("xb") as stream:
                while block := response.read(1024 * 1024):
                    stream.write(block)
            if path.stat().st_size != entry["size"]:
                raise ValueError(f"wheel size differs from lock: {entry['path']}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError(f"wheel digest differs from lock: {entry['path']}")
        manifest = {
            "format_version": 1,
            "target": lock["target"],
            "wheels": [
                {
                    "path": entry["path"],
                    "sha256": entry["sha256"],
                    "source": entry["source"],
                    "license": entry["license"],
                }
                for entry in entries
            ],
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    except BaseException:
        # Only this invocation created the directory; never leave a partial set.
        import shutil

        shutil.rmtree(output, ignore_errors=True)
        raise
    return manifest


def write_constraints(lock, path):
    """Emit the exact pins build_bundle treats as its constraint authority."""
    path = Path(path)
    if path.exists():
        raise ValueError("constraints already exist; choose a new path")
    lines = [f"{name}=={version}" for name, version in sorted(lock["pins"].items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--constraints", type=Path, default=None)
    args = parser.parse_args()
    lock = load_lock(args.lock)
    manifest = stage(lock, args.output, download=args.download)
    if args.constraints is not None:
        write_constraints(lock, args.constraints)
    print(json.dumps({"verified_wheels": len(manifest["wheels"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
