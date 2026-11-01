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
DARWIN_TARGET = {"system": "darwin", "machine": "arm64", "python": "cp313", "min_macos": "11.0"}


# Retain the exact semantics of previously published 2.27 manifests.
SUPPORTED_GLIBC = ("2.17", "2.27")
# macOS deployment targets a lock may declare. Each entry is the *minimum* the
# wheel set is built and verified against; the derived tag list below walks
# upward from it, because a wheel built for an older macOS runs on a newer one.
SUPPORTED_MACOS = ("11.0",)


def dependency_target(target):
    """Validate a dependency target and return its numeric minimum platform.

    Linux returns the glibc tuple, macOS the (major, minor) macOS version. Both
    are the floor the staged wheels must be compatible with, not the host that
    happened to build them.
    """
    if not isinstance(target, dict):
        raise TypeError("dependency target must be an object")
    system = target.get("system")
    if system == "linux":
        if target.get("glibc") not in SUPPORTED_GLIBC:
            raise ValueError("unsupported dependency format or target")
        if target != {**TARGET, "glibc": target["glibc"]}:
            raise ValueError("unsupported dependency format or target")
        return tuple(map(int, target["glibc"].split(".")))
    if system == "darwin":
        minimum = target.get("min_macos")
        if minimum not in SUPPORTED_MACOS:
            raise ValueError("unsupported dependency format or target")
        if target != DARWIN_TARGET:
            raise ValueError("unsupported dependency format or target")
        return tuple(map(int, minimum.split(".")))
    raise ValueError("unsupported dependency format or target")


def _linux_platforms(glibc):
    minor = int(glibc.split(".")[1])
    platforms = [f"manylinux_2_{n}_x86_64" for n in range(minor, 4, -1)]
    platforms += ["manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64"]
    return platforms


def _macos_platforms(minimum):
    """Every macOS arm64 tag at or above the lock's floor.

    A wheel tagged macosx_11_0_arm64 also satisfies a 13.0 host, so the set has
    to include the older tag rather than only the newest one.
    """
    major, minor = (int(part) for part in minimum.split("."))
    platforms = [f"macosx_{major}_{minor}_arm64"]
    platforms += [f"macosx_{m}_0_arm64" for m in range(major + 1, 16)]
    return platforms


def _tags(target):
    """Compatibility tags a dependency wheel must intersect for this target."""
    if target["system"] == "darwin":
        platforms = _macos_platforms(target["min_macos"])
    else:
        platforms = _linux_platforms(target["glibc"])
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
        if manifest["format_version"] != 1:
            raise ValueError("unsupported dependency format or target")
        dependency_target(manifest["target"])
        entries = manifest["wheels"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("dependency wheels must be a nonempty list")
        found, expected = {}, {"manifest.json"}
        supported = _tags(manifest["target"])
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
                target = manifest["target"]
                if target["system"] == "darwin":
                    floor = f"macOS {target['min_macos']} arm64"
                else:
                    floor = f"glibc{target['glibc']} x86_64"
                raise ValueError(f"dependency wheel is incompatible with cp313 {floor}")
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
