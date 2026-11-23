"""Stage the locked inputs build-client-tree.py verifies, from public sources.

Build-time only. Downloads are restricted to the URLs recorded in
native/runtime-lock.json, native/tool-lock.json and native/dependency-lock.json;
every byte is checked against the locked size and digest. Never installs,
executes or repacks anything, and never rewrites a lock.

Output is the <inputs> layout the tree builder reads:

    <inputs>/python.tar.gz     runtime-lock.json (target CPython archive)
    <inputs>/uv.whl            tool-lock.json    (build tool, never shipped)
    <inputs>/dependencies/     dependency-lock.json wheels and manifest.json

The caller still builds <inputs>/wheels from its own source; that is the only
input this script cannot fetch, because it is the repository's product.
"""

import argparse
import hashlib
import importlib.util
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def load(name, filename):
    """Import a sibling build script, which is hyphenated and not importable."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch(path, *, url, sha256, size):
    if not isinstance(url, str) or urlparse(url).scheme != "https":
        raise ValueError("HTTPS lock URL required")
    with urllib.request.urlopen(url, timeout=120) as response, path.open("xb") as stream:
        while block := response.read(1024 * 1024):
            stream.write(block)
    if path.stat().st_size != size:
        raise ValueError(f"size differs from lock: {path.name}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise ValueError(f"digest differs from lock: {path.name}")


def resolve_uv_url(version, filename):
    """The tool lock records provenance and digest but not the file URL.

    The filename is the identity: uv publishes several platform wheels per
    version, so only that exact name may be selected, and its digest is then
    checked against the lock like any other input.
    """
    with urllib.request.urlopen(f"https://pypi.org/pypi/uv/{version}/json", timeout=60) as handle:
        data = json.load(handle)
    for entry in data.get("urls", []):
        if entry.get("filename") == filename:
            return entry["url"]
    raise ValueError(f"uv {version} does not publish {filename}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    tree = load("build_client_tree", "build-client-tree.py")
    dependencies = load("stage_native_dependencies", "stage-native-dependencies.py")
    target, _, tool_lock_name, dependency_lock_name = tree.build_target()
    runtime = json.loads((args.locks / "runtime-lock.json").read_text())["targets"][target]
    tools = json.loads((args.locks / tool_lock_name).read_text())
    dependency_lock = dependencies.load_lock(args.locks / dependency_lock_name)

    output = args.output
    if output.exists():
        raise ValueError("output already exists; choose a new inputs directory")
    output.mkdir(parents=True)
    try:
        # The lock entry already carries url/sha256/size, so it is the argument
        # set; the destination is the only value this script chooses.
        fetch(output / "python.tar.gz", **runtime)
        fetch(
            output / "uv.whl",
            url=resolve_uv_url(tools["version"], tools["filename"]),
            sha256=tools["sha256"],
            size=tools["size"],
        )
        manifest = dependencies.stage(
            dependency_lock, output / "dependencies", download=True
        )
    except BaseException:
        # Only this invocation created the directory; never leave a partial set.
        import shutil

        shutil.rmtree(output, ignore_errors=True)
        raise
    print(
        json.dumps(
            {
                "target": target,
                "python_sha256": runtime["sha256"],
                "uv_sha256": tools["sha256"],
                "verified_wheels": len(manifest["wheels"]),
                "output": str(output),
            }
        )
    )


if __name__ == "__main__":
    main()
