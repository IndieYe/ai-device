"""Prepare locked Linux CPython3.13 wheels; downloading requires --download.

Build-time only. Never installs, executes wheels, changes the uv lock or cleans
existing files. The acceptance script installs the exact manifest files offline.
"""

import argparse
import hashlib
import json
import re
import tomllib
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from packaging.utils import canonicalize_name, parse_wheel_filename


def select_wheels(lock, requirements):
    names = set()
    for line in requirements.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^ ;]+)(?:\s*;.*)?", line)
        if not match:
            raise ValueError("only exact locked requirements accepted")
        names.add((canonicalize_name(match[1]), match[2]))
    selected = []
    for name, version in sorted(names):
        package = next(p for p in lock["package"] if p["name"] == name and p["version"] == version)
        candidates = []
        for item in package.get("wheels", []):
            filename = Path(urlparse(item["url"]).path).name
            tags = parse_wheel_filename(filename)[3]
            ranks = []
            for tag in tags:
                platform = tag.platform
                if platform != "any" and not (
                    platform.startswith("manylinux") and platform.endswith("_x86_64")
                ):
                    continue
                # cp313t is explicitly excluded: the pinned runtime is not free-threaded.
                if tag.interpreter == "cp313" and tag.abi == "cp313":
                    ranks.append(0)
                elif tag.abi == "abi3" and re.fullmatch(r"cp3\d+", tag.interpreter):
                    if 36 <= int(tag.interpreter[2:]) <= 313:
                        ranks.append(1)
                elif tag.interpreter == "py3" and tag.abi == "none":
                    ranks.append(2)
            if ranks:
                candidates.append((min(ranks), filename, item))
        if not candidates:
            raise ValueError(f"no compatible locked wheel: {name}=={version}")
        _, filename, item = min(candidates, key=lambda candidate: candidate[:2])
        selected.append({"name": filename, **item})
    if not selected:
        raise ValueError("empty locked requirements")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    entries = select_wheels(tomllib.loads(args.lock.read_text()), args.requirements.read_text())
    if args.manifest.exists():
        raise ValueError("manifest already exists; do not overwrite build evidence")
    args.output.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        path = args.output / entry["name"]
        if not path.exists():
            if not args.download:
                raise ValueError(f"missing wheel: {entry['name']}; explicit --download required")
            if urlparse(entry["url"]).scheme != "https":
                raise ValueError("HTTPS lock URL required")
            with (
                urllib.request.urlopen(entry["url"], timeout=60) as response,
                path.open("xb") as stream,
            ):
                while block := response.read(1024 * 1024):
                    stream.write(block)
        if path.stat().st_size != entry["size"] or (
            hashlib.sha256(path.read_bytes()).hexdigest() != entry["hash"].removeprefix("sha256:")
        ):
            raise ValueError(f"wheel differs from lock: {path.name}; retained for inspection")
    with args.manifest.open("x") as stream:
        json.dump(entries, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"verified_wheels": len(entries), "manifest": str(args.manifest)}))


if __name__ == "__main__":
    main()
