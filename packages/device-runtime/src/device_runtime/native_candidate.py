"""Authenticated, script-free Debian candidate staging. Never installs or activates.

Trust roots are supplied out of band. The native extra provides cryptography;
no bundled key, network lookup or executable from the candidate is trusted.
"""

import argparse
import base64
import binascii
import hashlib
import json
import os
import posixpath
import re
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from packaging.version import InvalidVersion, Version

from .bundle import inspect_bundle
from .protocol import canonical

MAX_PACKAGE = 1_000_000_000
MAX_EXPANDED = 4_000_000_000
MAX_MEMBERS = 100000
MAX_ENVELOPE = 256000


class CandidateError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CandidateError("duplicate JSON key")
        result[key] = value
    return result


def _json(data):
    try:
        return json.loads(data, object_pairs_hook=_object, parse_constant=lambda _: _invalid())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateError("invalid release JSON") from exc


def _invalid():
    raise CandidateError("non-finite JSON number")


def _read_envelope(path):
    # Bound the read itself: a prior stat does not constrain concurrent writes.
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_ENVELOPE + 1)
    if len(data) > MAX_ENVELOPE:
        raise CandidateError("release envelope too large")
    return _json(data)


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise CandidateError("exact SHA256 required")
    return value


def verify_release(
    envelope, trusted_keys, *, release_id, target, source_version, bootstrap_version
):
    """Authenticate canonical payload with externally configured raw Ed25519 keys."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(envelope, dict) or set(envelope) != {"payload", "key_id", "signature"}:
        raise CandidateError("release envelope fields differ")
    key_id = envelope["key_id"]
    if not isinstance(key_id, str) or key_id not in trusted_keys:
        raise CandidateError("unknown signing key")
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise CandidateError("release payload must be an object")
    try:
        signature = base64.b64decode(envelope["signature"], validate=True)
        key = base64.b64decode(trusted_keys[key_id], validate=True)
        Ed25519PublicKey.from_public_bytes(key).verify(
            signature, canonical(payload).encode("utf-8")
        )
    except (InvalidSignature, ValueError, TypeError, binascii.Error) as exc:
        raise CandidateError("release signature invalid") from exc
    required = {
        "schema",
        "release_id",
        "key_id",
        "versions",
        "bootstrap_min",
        "target",
        "allowed_sources",
        "required_space",
        "compatibility",
        "package",
    }
    if (
        not required <= payload.keys()
        or type(payload["schema"]) is not int
        or payload["schema"] != 1
    ):
        raise CandidateError("unsupported release schema")
    if (
        payload["release_id"] != release_id
        or payload["key_id"] != key_id
        or payload["target"] != target
    ):
        raise CandidateError("release identity or target differs")
    if not isinstance(payload["versions"], dict) or set(payload["versions"]) != {
        "device",
        "bash",
        "fs",
    }:
        raise CandidateError("three exact component versions required")
    if (
        not isinstance(payload["allowed_sources"], list)
        or source_version not in payload["allowed_sources"]
    ):
        raise CandidateError("source version not allowed")
    try:
        if Version(bootstrap_version) < Version(payload["bootstrap_min"]):
            raise CandidateError("bootstrap too old")
        for version in payload["versions"].values():
            Version(version)
    except (InvalidVersion, TypeError) as exc:
        raise CandidateError("invalid release version") from exc
    if (
        type(payload["required_space"]) is not int
        or not 0 < payload["required_space"] <= 20_000_000_000
    ):
        raise CandidateError("invalid space requirement")
    compatibility = payload["compatibility"]
    if not isinstance(compatibility, dict) or any(
        not isinstance(compatibility.get(name), list)
        or not all(type(version) is int for version in compatibility[name])
        or 1 not in compatibility[name]
        for name in ("protocol", "state")
    ):
        raise CandidateError("release protocol/state compatibility unsupported")
    package = payload["package"]
    fields = {
        "name",
        "version",
        "architecture",
        "size",
        "sha256",
        "data_sha256",
        "payload_root",
        "manifest_sha256",
    }
    if not isinstance(package, dict) or not fields <= package.keys():
        raise CandidateError("package descriptor incomplete")
    if (
        package["name"] != "ai-device-native-client"
        or package["architecture"] != "amd64"
        or target != "linux-amd64-deb"
    ):
        raise CandidateError("unsupported native package target")
    if type(package["size"]) is not int or not 0 < package["size"] <= MAX_PACKAGE:
        raise CandidateError("package exceeds size budget")
    for name in ("sha256", "data_sha256", "manifest_sha256"):
        _sha(package[name])
    root = package["payload_root"]
    if (
        not isinstance(root, str)
        or not re.fullmatch(r"opt/ai-device/packages/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", root)
        or root.endswith(("/.", "/.."))
    ):
        raise CandidateError("invalid versioned payload root")
    return payload


def _deb_data(package, output, expected, descriptor):
    """Read only standard ar DEB members; never invoke dpkg or maintainer scripts."""
    seen = set()
    control = output.with_name("control.tar")
    with package.open("rb") as stream:
        if stream.read(8) != b"!<arch>\n":
            raise CandidateError("not a Debian ar archive")
        while header := stream.read(60):
            if len(header) != 60 or header[58:] != b"`\n":
                raise CandidateError("invalid ar header")
            try:
                name = header[:16].decode("ascii").strip().removesuffix("/")
                size = int(header[48:58].decode("ascii").strip())
            except (ValueError, UnicodeError) as exc:
                raise CandidateError("invalid ar member") from exc
            if size < 0 or size > MAX_PACKAGE or name in seen:
                raise CandidateError("duplicate or oversized ar member")
            if name not in {
                "debian-binary",
                "control.tar.gz",
                "control.tar.xz",
                "control.tar.zst",
                "data.tar.gz",
                "data.tar.xz",
                "data.tar.zst",
                "data.tar",
            }:
                raise CandidateError("unsupported Debian member/compression")
            seen.add(name)
            if name == "debian-binary":
                if size != 4 or stream.read(size) != b"2.0\n":
                    raise CandidateError("unsupported Debian format")
            else:
                data = name.startswith("data.")
                with (output if data else control).open("xb") as destination:
                    remaining = size
                    while remaining:
                        block = stream.read(min(1024 * 1024, remaining))
                        if not block:
                            raise CandidateError("truncated Debian member")
                        destination.write(block)
                        remaining -= len(block)
            if size % 2 and stream.read(1) != b"\n":
                raise CandidateError("invalid ar padding")
    if (
        len(seen) != 3
        or "debian-binary" not in seen
        or sum(n.startswith("control.") for n in seen) != 1
        or sum(n.startswith("data.") for n in seen) != 1
    ):
        raise CandidateError("Debian archive must contain one control and one data member")
    if _digest(output) != expected:
        raise CandidateError("signed data archive digest differs")
    for path, prefix, budget in ((control, "control.", 8_000_000), (output, "data.", MAX_EXPANDED)):
        if any(name.startswith(prefix) and name.endswith(".zst") for name in seen):
            from zstandard import ZstdDecompressor

            decoded = path.with_name(path.name + ".decoded")
            total = 0
            with (
                path.open("rb") as source,
                ZstdDecompressor(max_window_size=134217728).stream_reader(source) as stream,
                decoded.open("xb") as destination,
            ):
                while block := stream.read(1024 * 1024):
                    total += len(block)
                    if total > budget:
                        raise CandidateError("zstd archive exceeds extraction budget")
                    destination.write(block)
            decoded.replace(path)
    with tarfile.open(control, "r|*") as archive:
        metadata = None
        count = expanded = 0
        for member in archive:
            count += 1
            expanded += member.size
            if count > 1000 or expanded > 8_000_000:
                raise CandidateError("control archive exceeds budget")
            if _name(member.name) != "control":
                continue
            if metadata is not None or not member.isfile() or member.size > 64000:
                raise CandidateError("invalid or duplicate Debian control metadata")
            metadata = archive.extractfile(member).read().decode("utf-8")
        fields = {}
        for line in (metadata or "").splitlines():
            if line.startswith((" ", "\t")) or not line:
                continue
            if ":" not in line:
                raise CandidateError("invalid Debian control field")
            key, value = line.split(":", 1)
            if key in fields:
                raise CandidateError("duplicate Debian control field")
            fields[key] = value.strip()
        if any(
            fields.get(key) != descriptor[field]
            for key, field in (
                ("Package", "name"),
                ("Version", "version"),
                ("Architecture", "architecture"),
            )
        ):
            raise CandidateError("Debian control differs from signed descriptor")


def _name(value):
    if not isinstance(value, str) or "\x00" in value or "\\" in value or value.startswith("/"):
        raise CandidateError("unsafe archive path")
    while value.startswith("./"):
        value = value[2:]
    value = value.rstrip("/")
    if value in ("", "."):
        return ""
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise CandidateError("unsafe archive path")
    return value


def _extract(archive, root):
    names, links, total = set(), [], 0
    with tarfile.open(archive, "r|*") as source:
        for member in source:
            name = _name(member.name)
            if not name and member.isdir():
                continue
            if not name or name in names:
                raise CandidateError("duplicate archive path")
            names.add(name)
            total += member.size
            if len(names) > MAX_MEMBERS or total > MAX_EXPANDED or member.size < 0:
                raise CandidateError("extraction budget exceeded")
            if ((member.isfile() or member.isdir()) and member.mode & 0o7022) or not (
                member.isdir() or member.isfile() or member.issym() or member.islnk()
            ):
                raise CandidateError("unsupported archive type or permissions")
            path = root / name
            # Links are deferred, so no extraction writes through a link.
            if member.issym() or member.islnk():
                if member.issym():
                    if member.linkname.startswith("/") or "\\" in member.linkname:
                        raise CandidateError("unsafe symbolic link")
                    target = posixpath.normpath(
                        posixpath.join(posixpath.dirname(name), member.linkname)
                    )
                    _name(target)
                else:
                    target = _name(member.linkname)
                if not target:
                    raise CandidateError("empty archive link target")
                links.append((member, path, target))
            elif member.isdir():
                path.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with source.extractfile(member) as stream, path.open("xb") as destination:
                    shutil.copyfileobj(stream, destination, 1024 * 1024)
                path.chmod(0o700 if member.mode & 0o111 else 0o600)
    link_paths = {path.relative_to(root).as_posix() for _, path, _ in links}
    for member, path, target in links:
        if any(
            parent.as_posix() in link_paths
            for parent in PurePosixPath(path.relative_to(root)).parents
        ):
            raise CandidateError("archive link ancestor")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if member.islnk():
            if (
                target in link_paths
                or any(parent.as_posix() in link_paths for parent in PurePosixPath(target).parents)
                or not (root / target).is_file()
            ):
                raise CandidateError("hardlink target must be a regular extracted file")
            os.link(root / target, path)
        else:
            path.symlink_to(member.linkname)
    # Resolve chained links only after all have been created. No escaping or
    # dangling links are accepted in a prepared immutable candidate.
    for _, path, _ in links:
        try:
            if not path.resolve(strict=True).is_relative_to(root.resolve()):
                raise CandidateError("archive link escapes candidate")
        except (OSError, RuntimeError) as exc:
            raise CandidateError("invalid archive link chain") from exc
    return {"members": len(names), "expanded_bytes": total}


def prepare_candidate(
    package,
    envelope_path,
    destination,
    *,
    trusted_keys,
    release_id,
    target,
    source_version,
    bootstrap_version,
):
    """Prepare authenticated extracted payload only; no venv, script or active write."""
    package, envelope_path, destination = map(Path, (package, envelope_path, destination))
    envelope = _read_envelope(envelope_path)
    payload = verify_release(
        envelope,
        trusted_keys,
        release_id=release_id,
        target=target,
        source_version=source_version,
        bootstrap_version=bootstrap_version,
    )
    descriptor = payload["package"]
    if os.path.lexists(destination):
        raise CandidateError("candidate destination already exists")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise CandidateError("existing candidate parent required")
    if shutil.disk_usage(destination.parent).free < payload["required_space"]:
        raise CandidateError("insufficient candidate space")
    # mkdir is an exclusive reservation. Never replace an existing candidate.
    destination.mkdir(mode=0o700)
    try:
        snapshot = destination / "package.deb"
        with package.open("rb") as stream, snapshot.open("xb") as output:
            remaining = descriptor["size"]
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    raise CandidateError("package shorter than signed size")
                output.write(block)
                remaining -= len(block)
            if stream.read(1):
                raise CandidateError("package larger than signed size")
        if _digest(snapshot) != descriptor["sha256"]:
            raise CandidateError("signed package digest differs")
        data = destination / "data.tar"
        _deb_data(snapshot, data, descriptor["data_sha256"], descriptor)
        root = destination / "root"
        root.mkdir(mode=0o700)
        observation = _extract(data, root)
        bundle = root / descriptor["payload_root"] / "bundle"
        if _digest(bundle / "manifest.json") != descriptor["manifest_sha256"]:
            raise CandidateError("signed payload manifest differs")
        manifest = inspect_bundle(bundle)
        if manifest["format_version"] != 3 or manifest["target"]["system"] != "linux":
            raise CandidateError("complete offline Linux payload required")
        if {key: value["version"] for key, value in manifest["components"].items()} != payload[
            "versions"
        ]:
            raise CandidateError("payload component versions differ")
        receipt = {
            "schema": 1,
            "state": "authenticated_extracted",
            "release_id": release_id,
            "release_sha256": hashlib.sha256(canonical(payload).encode()).hexdigest(),
            "package_sha256": descriptor["sha256"],
            "payload_root": descriptor["payload_root"],
            "observation": observation,
            "activated": False,
            "venv_prepared": False,
        }
        (destination / "release.json").write_text(canonical(envelope), encoding="utf-8")
        # Record all extracted files before the completion receipt is durable.
        for path in destination.rglob("*"):
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
        for path in sorted(
            (p for p in destination.rglob("*") if p.is_dir() and not p.is_symlink()),
            key=lambda p: len(p.parts),
            reverse=True,
        ) + [destination, destination.parent]:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        # Publish completion only after all candidate bytes and directory
        # entries are durable. Interrupted stages without it are incomplete.
        temporary = destination / ".candidate-receipt"
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(canonical(receipt))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination / "candidate.json")
        temporary.unlink()
        fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return receipt
    except BaseException:
        shutil.rmtree(destination)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "package",
        "envelope",
        "destination",
        "trusted-keys",
        "release-id",
        "source-version",
        "bootstrap-version",
    ):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    keys = _json(Path(args.trusted_keys).read_bytes())
    result = prepare_candidate(
        args.package,
        args.envelope,
        args.destination,
        trusted_keys=keys,
        release_id=args.release_id,
        target="linux-amd64-deb",
        source_version=args.source_version,
        bootstrap_version=args.bootstrap_version,
    )
    print(canonical(result))


if __name__ == "__main__":
    main()
