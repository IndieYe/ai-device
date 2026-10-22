"""Stage validated files and reconcile public PyPI hashes; never uploads."""

import argparse
import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from capture_evidence import validate_candidate

GROUPS = {"bash": "ai-device-bash", "fs": "ai-device-fs", "device": "ai-device"}


def stage(root, target):
    validate_candidate(root)
    if target.exists():
        raise ValueError("release directory must not already exist")
    manifest = {}
    for group, name in GROUPS.items():
        files = sorted((root / "dist").glob(name.replace("-", "_") + "-*"))
        if len(files) != 2:
            raise ValueError(f"expected exactly two files for {name}")
        for source in files:
            destination = target / group / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            manifest[str(destination.relative_to(target))] = hashlib.sha256(
                destination.read_bytes()
            ).hexdigest()
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def check_files(target):
    manifest = json.loads((target / "manifest.json").read_text())
    actual = {
        str(p.relative_to(target)) for g in GROUPS for p in (target / g).iterdir()
    }
    if actual != set(manifest) or len(actual) != 6:
        raise ValueError("release file set mismatch")
    for name, digest in manifest.items():
        if hashlib.sha256((target / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"release hash mismatch: {name}")
    return manifest


def check_public(target, group):
    manifest = check_files(target)
    files = {Path(n).name: h for n, h in manifest.items() if n.startswith(group + "/")}
    wheel = next(n for n in files if n.endswith(".whl"))
    version = wheel.split("-")[1]
    url = f"https://pypi.org/pypi/{GROUPS[group]}/{version}/json"
    # Only repeat read-only queries. Unknown upload results require human reconciliation.
    deadline = time.monotonic() + 300
    while True:
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                data = json.load(response)
            public = {f["filename"]: f for f in data["urls"]}
            if set(public) != set(files):
                raise ValueError("PyPI file set differs from release")
            for name, digest in files.items():
                entry = public[name]
                if entry["digests"]["sha256"] != digest or entry.get("yanked"):
                    raise ValueError(f"PyPI metadata mismatch: {name}")
                download = entry["url"]
                if not download.startswith("https://files.pythonhosted.org/"):
                    raise ValueError("unexpected download host")
                with urllib.request.urlopen(download, timeout=30) as response:
                    downloaded = hashlib.file_digest(response, "sha256").hexdigest()
                if downloaded != digest:
                    raise ValueError(f"download mismatch: {name}")
            print(f"Public hashes and downloads verified: {GROUPS[group]} {version}")
            return
        except urllib.error.HTTPError as error:
            if error.code != 404 or time.monotonic() >= deadline:
                raise
            time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stage", "check", "public"])
    parser.add_argument("--directory", type=Path, default=Path("release"))
    parser.add_argument("--group", choices=list(GROUPS))
    args = parser.parse_args()
    if args.action == "stage":
        stage(Path(__file__).resolve().parents[1], args.directory)
    elif args.action == "check":
        check_files(args.directory)
    else:
        if args.group is None:
            parser.error("public requires --group")
        check_public(args.directory, args.group)


if __name__ == "__main__":
    main()
