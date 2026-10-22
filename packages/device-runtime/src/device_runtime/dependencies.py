"""Versioned, complete third-party wheel sets; never download or execute artifacts."""

import hashlib
import json
from email.parser import BytesParser
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

TARGET = {"system": "linux", "machine": "x86_64", "python": "cp313", "glibc": "2.17"}


def _tags():
    platforms = [f"manylinux_2_{minor}_x86_64" for minor in range(17, 4, -1)]
    platforms += ["manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64"]
    return set(cpython_tags((3, 13), platforms=platforms)) | set(
        compatible_tags((3, 13), interpreter="cp313", platforms=platforms)
    )


def inspect_dependencies(directory, pins):
    """Require exactly one compatible wheel per pin and provenance for every wheel.

    The resolver still verifies dependency relationships during installation; matching pins
    alone does not prove a valid dependency closure or working native image codecs.
    """
    directory = Path(directory)
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["format_version"] != 1 or manifest["target"] != TARGET:
            raise ValueError("unsupported dependency format or target")
        entries = manifest["wheels"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("dependency wheels must be a nonempty list")
        found, expected = {}, {"manifest.json"}
        supported = _tags()
        for entry in entries:
            filename = entry["path"]
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise ValueError("dependency wheel path must be a plain filename")
            path = directory / filename
            name, version, _, tags = parse_wheel_filename(filename)
            name = canonicalize_name(name)
            if name in found or name not in pins or version != Version(pins[name]):
                raise ValueError("dependency wheel must match exactly one constraint pin")
            if not tags & supported:
                raise ValueError("dependency wheel is incompatible with cp313 glibc2.17 x86_64")
            for key in ("source", "license"):
                if not isinstance(entry[key], str) or not entry[key].strip():
                    raise ValueError("dependency source and license are required")
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("dependency wheel hash mismatch")
            with ZipFile(path) as archive:
                metadata = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
                if len(metadata) != 1:
                    raise ValueError("dependency wheel must have one distribution metadata file")
                message = BytesParser().parsebytes(archive.read(metadata[0]))
                if (
                    canonicalize_name(message["Name"]) != name
                    or Version(message["Version"]) != version
                ):
                    raise ValueError("dependency filename and metadata disagree")
            found[name] = filename
            expected.add(filename)
        if set(found) != set(pins):
            raise ValueError("dependency wheel set must cover every constraint pin")
        actual = {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()}
        if actual != expected:
            raise ValueError("dependency directory has missing or unlisted files")
        return manifest
    except (OSError, KeyError, TypeError, AttributeError, BadZipFile, UnicodeError) as exc:
        raise ValueError("incomplete or invalid dependency directory") from exc
