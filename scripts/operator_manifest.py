"""Record an already prepared release; never installs, activates or edits a profile."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

PROBE = """import importlib.metadata as m,json,platform,sys,hashlib
from pathlib import Path
d=m.distribution("ai-device")
package=Path(d.locate_file("device_runtime"))
if not package.is_dir():
    raise ValueError("non-wheel package layout")
files={"device_runtime/"+str(f.relative_to(package)):hashlib.sha256(f.read_bytes()).hexdigest()
       for f in package.rglob("*.py") if f.is_file()}
print(json.dumps({"python_version":platform.python_version(),"platform":sys.platform,
"machine":platform.machine(),"runtime_files":files,"packages":sorted(
[{"name":d.metadata["Name"],"version":d.version} for d in m.distributions()],key=lambda x:x["name"].lower())}))"""


def build(release):
    release = release.absolute()
    if release.is_symlink() or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", release.name
    ):
        raise ValueError("invalid release directory")
    roles = {}
    for role in ("mcp", "ops"):
        relative = f"{role}/venv/bin/python"
        result = subprocess.run(
            [str(release / relative), "-I", "-B", "-c", PROBE],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        inventory = json.loads(result.stdout)
        packages = {
            p["name"].lower().replace("_", "-"): p["version"]
            for p in inventory["packages"]
        }
        if "ai-device" not in packages:
            raise ValueError("ai-device not installed")
        roles[role] = {"python": relative, **inventory}
    versions = [
        {p["name"]: p["version"] for p in r["packages"]}.get("ai-device")
        for r in roles.values()
    ]
    if versions[0] != versions[1] or versions[0] is None:
        raise ValueError("role versions differ")
    wheels = []
    device_wheels = []
    for path in sorted((release / "wheels").glob("*.whl")):
        if path.is_symlink():
            raise ValueError("wheel must be a regular file")
        with zipfile.ZipFile(path) as wheel:
            metadata_paths = [
                n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")
            ]
            if len(metadata_paths) != 1:
                raise ValueError("invalid wheel metadata")
            metadata = BytesParser().parsebytes(wheel.read(metadata_paths[0]))
            if metadata["Name"].lower().replace("_", "-") == "ai-device":
                files = {
                    n: hashlib.sha256(wheel.read(n)).hexdigest()
                    for n in wheel.namelist()
                    if n.startswith("device_runtime/") and n.endswith(".py")
                }
                device_wheels.append((metadata["Version"], files))
        wheels.append(
            {
                "path": f"wheels/{path.name}",
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    if not wheels:
        raise ValueError("wheel evidence required")
    if len(device_wheels) != 1:
        raise ValueError("exactly one ai-device wheel required")
    version, files = device_wheels[0]
    if version != versions[0] or not files:
        raise ValueError("wheel identity mismatch")
    for role in roles.values():
        if role["runtime_files"] != files:
            raise ValueError("installed runtime differs from wheel")
    return {
        "schema_version": 1,
        "release_id": release.name,
        "roles": roles,
        "wheels": wheels,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    args = parser.parse_args()
    manifest = build(args.release)
    save(args.release, manifest)


def save(release, manifest):
    # Complete private temporary file then no-clobber publication.
    fd, temp = tempfile.mkstemp(prefix=".release-", dir=release)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(manifest, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, release / "release.json")
    finally:
        os.unlink(temp)


if __name__ == "__main__":
    main()
