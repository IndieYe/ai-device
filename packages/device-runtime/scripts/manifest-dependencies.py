"""Fetch exact third-party wheels and record verified PyPI digests and licenses.

Run only inside the pinned cp313 manylinux2014 build container. Pillow is built
separately; no source fallback is allowed for the remaining pinned artifacts.
"""

import hashlib
import json
import sys
from email.parser import BytesParser
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

PILLOW_SOURCE = (
    "https://pypi.org/project/pillow/12.3.0/#files; sdist sha256="
    "3b8182a766685eaa002637e28b4ec8d6b18819a0c71f579bf0dbaa5830297cce; "
    "JPEG https://www.ijg.org/files/jpegsrc.v9f.tar.gz sha256="
    "04705c110cb2469caa79fb71fba3d7bf834914706e9641a4589485c1f832565b; "
    "WebP https://storage.googleapis.com/downloads.webmproject.org/releases/webp/"
    "libwebp-1.6.0.tar.gz sha256="
    "e4ab7009bf0629fd11982d4c2aa83964cf244cffba7347ecd39019a9e38c4564"
)

PINS = {
    "annotated-types": "0.8.0",
    "packaging": "26.3",
    "psutil": "7.2.2",
    "pydantic": "2.13.5",
    "pydantic-core": "2.46.5",
    "typing-extensions": "4.16.0",
    "typing-inspection": "0.4.4",
    "websockets": "17.2",
}


def main(directory):
    supported = list(sys_tags())
    ranks = {tag: index for index, tag in enumerate(supported)}
    origins = {}
    for name, version in PINS.items():
        with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as response:
            data = json.load(response)
        candidates = []
        for entry in data["urls"]:
            if entry["packagetype"] != "bdist_wheel" or entry.get("yanked"):
                continue
            tags = parse_wheel_filename(entry["filename"])[3]
            scores = [ranks[tag] for tag in tags if tag in ranks]
            if scores:
                candidates.append((min(scores), entry["filename"], entry))
        if not candidates:
            raise ValueError(f"no compatible wheel for {name}=={version}")
        entry = min(candidates, key=lambda x: (x[0], x[1]))[2]
        with urlopen(entry["url"], timeout=60) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != entry["digests"]["sha256"]:
            raise ValueError("PyPI digest mismatch")
        (directory / entry["filename"]).write_bytes(content)
        origins[entry["filename"]] = entry["url"]
    wheels = []
    for path in sorted(directory.glob("*.whl")):
        with ZipFile(path) as archive:
            metadata = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
            message = BytesParser().parsebytes(archive.read(metadata))
            license_name = message.get("License-Expression") or message.get("License")
            if not license_name:
                license_name = "; ".join(
                    c for c in message.get_all("Classifier", []) if c.startswith("License ::")
                )
            if not license_name:
                raise ValueError(
                    f"missing license metadata: {path.name}; inspect distribution manually"
                )
        wheels.append(
            {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source": origins.get(path.name, PILLOW_SOURCE),
                "license": license_name,
            }
        )
    manifest = {
        "format_version": 1,
        "target": {"system": "linux", "machine": "x86_64", "python": "cp313", "glibc": "2.17"},
        "wheels": wheels,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
