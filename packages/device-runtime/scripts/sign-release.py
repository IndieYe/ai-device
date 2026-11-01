"""Sign a built native package into a release envelope.

This is the production signer, unlike accept-native-candidate.py which mints an
ephemeral key per run. The private key is read from a path you name; it is never
printed, copied, or embedded, and this script does not install or activate
anything. Signing binds the package bytes, the compressed data member, the
control identity and the bundle manifest, so a repacked package cannot reuse an
existing envelope.

Example:
 python sign-release.py \\
   --package out/ai-device-native-client_0.2.4-1_amd64.deb \\
   --key ~/device-release-keys/k1.private.pem \\
   --key-id k1 \\
   --release-id 2026-11-01-tencent-sh \\
   --allowed-source 0.2.3 --source-version 0.2.4 \\
   --output out/release.json
"""

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
import stat
import sys
import tarfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from device_runtime.native_candidate import MAX_PACKAGE, TARGET, _json
from device_runtime.protocol import canonical

MAX_MANIFEST = 256_000


def _ar_members(path):
    """Return {name: bytes} for a Debian ar archive without spawning ar."""
    parts = {}
    with path.open("rb") as stream:
        if stream.read(8) != b"!<arch>\n":
            raise SystemExit(f"not a Debian archive: {path}")
        while header := stream.read(60):
            if len(header) < 60:
                raise SystemExit("truncated ar header")
            name = header[:16].decode().strip().rstrip("/")
            size = int(header[48:58].decode().strip())
            if not 0 <= size <= MAX_PACKAGE or name in parts:
                raise SystemExit(f"invalid ar member: {name}")
            parts[name] = stream.read(size)
            if size % 2:
                stream.read(1)
    return parts


def _open_tar(data_name, body):
    """Open the tar inside a dpkg data/control member by its compression suffix."""
    suffix = data_name.rsplit(".", 1)[-1]
    if suffix == "zst":
        from zstandard import ZstdDecompressor

        reader = ZstdDecompressor(max_window_size=134217728).stream_reader(io.BytesIO(body))
        # The selftest has to shut the reader down before the tar is closed: a
        # streamed tar keeps reading lazily. ``closing`` wraps the reader and
        # only exposes ``close``, so keep the underlying object too -- closing
        # the wrapper alone is not what the caller needs.
        return reader, tarfile.open(fileobj=reader, mode="r|")
    if suffix == "xz":
        import lzma

        return None, tarfile.open(fileobj=io.BytesIO(lzma.decompress(body)), mode="r:")
    if suffix == "gz":
        import gzip

        return None, tarfile.open(fileobj=io.BytesIO(gzip.decompress(body)), mode="r:")
    return None, tarfile.open(fileobj=io.BytesIO(body), mode="r:")


@contextlib.contextmanager
def _archive(data_name, body):
    """Yield the outer tar for a member, closing the decompressor with it."""
    closer, archive = _open_tar(data_name, body)
    try:
        with archive:
            yield archive
    finally:
        if closer is not None:
            # ``closer`` is the raw decompression reader, not a ``closing``
            # wrapper: calling ``closer.close`` would raise AttributeError and
            # turn a successful read into a signing failure.
            closer.close()


def _control(data_name, body):
    """Return the control fields of a control member."""
    fields = {}
    with _archive(data_name, body) as archive:
        for member in archive:
            if member.name.removeprefix("./") != "control":
                continue
            if not member.isfile() or member.size > MAX_MANIFEST:
                raise SystemExit("invalid control member")
            text = archive.extractfile(member).read().decode("utf-8")
            for line in text.splitlines():
                if line and not line.startswith((" ", "\t")) and ":" in line:
                    key, value = line.split(":", 1)
                    fields[key.strip()] = value.strip()
    for required in ("Package", "Version", "Architecture"):
        if not fields.get(required):
            raise SystemExit(f"control is missing {required}")
    return fields


def _manifest(data_name, body):
    """Read bundle/manifest.json out of the payload archive inside data.tar.

    data.tar holds opt/ai-device/payloads/<tree>.tar, which in turn holds
    bundle/manifest.json. Only one payload member may carry it, so a package
    with two candidate trees cannot be signed ambiguously.
    """
    found = None
    payload_root = None
    with _archive(data_name, body) as outer:
        for member in outer:
            name = member.name.removeprefix("./")
            # The payload archive is the tree being signed, e.g.
            # opt/ai-device/payloads/<tree>.tar. Only that member can carry the
            # manifest this release refers to.
            if not name.startswith("opt/ai-device/payloads/") or not name.endswith(".tar"):
                continue
            if not member.isfile():
                raise SystemExit(f"payload member is not a regular file: {name}")
            if member.size > MAX_PACKAGE:
                raise SystemExit("payload archive exceeds budget")
            inner_body = outer.extractfile(member).read()
            # The payload archive is stored uncompressed, but honour a suffix so
            # a compressed variant fails loudly here instead of silently.
            with _archive(name, inner_body) as inner:
                for item in inner:
                    if item.name.removeprefix("./") != "bundle/manifest.json":
                        continue
                    if found is not None or not item.isfile() or item.size > MAX_MANIFEST:
                        raise SystemExit("duplicate or oversized manifest")
                    found = inner.extractfile(item).read()
                    # Only the member that actually carries the manifest may be
                    # named as payload_root: <tree>.tar and <tree>-bootstrap.tar
                    # are both shipped, and only one of them is the bundle.
                    payload_root = name
    if found is None:
        raise SystemExit("bundle/manifest.json not found in payload")
    return found, payload_root


def _private_key(path):
    """Load an Ed25519 private key, refusing loose filesystem permissions."""
    if path.is_symlink():
        raise SystemExit(f"refused: {path} is a symlink")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise SystemExit(f"refused: {path} is not a regular file")
    if info.st_mode & 0o077:
        raise SystemExit(f"refused: {path} is readable beyond its owner")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise SystemExit(f"refused: {path} is not owned by the signing user")
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("refused: not an Ed25519 private key")
    return key


def main():
    parser = argparse.ArgumentParser(description="Sign a built native package.")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allowed-source", action="append", required=True)
    parser.add_argument("--source-version", required=True, help="version this release ships")
    parser.add_argument("--bootstrap-min", default=None)
    parser.add_argument("--required-space", type=int, default=2_000_000_000)
    args = parser.parse_args()

    package = args.package.resolve()
    if not 0 < package.stat().st_size <= MAX_PACKAGE:
        raise SystemExit("package exceeds size budget")

    parts = _ar_members(package)
    control_name = next((n for n in parts if n.startswith("control.tar")), None)
    data_name = next((n for n in parts if n.startswith("data.tar")), None)
    if control_name is None or data_name is None:
        raise SystemExit("package lacks control.tar or data.tar")

    fields = _control(control_name, parts[control_name])
    manifest_bytes, payload_root = _manifest(data_name, parts[data_name])
    manifest = _json(manifest_bytes)

    versions = {name: value["version"] for name, value in manifest["components"].items()}
    if versions.get("device") != args.source_version:
        raise SystemExit(
            f"refused: package ships device {versions.get('device')} "
            f"but --source-version is {args.source_version}"
        )

    payload = {
        "schema": 1,
        "release_id": args.release_id,
        "key_id": args.key_id,
        "versions": versions,
        "bootstrap_min": args.bootstrap_min or args.source_version,
        "target": TARGET,
        "allowed_sources": args.allowed_source,
        "required_space": args.required_space,
        "compatibility": {"protocol": [1], "state": [1]},
        "package": {
            "name": fields["Package"],
            "version": fields["Version"],
            "architecture": fields["Architecture"],
            "size": package.stat().st_size,
            "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
            # The digest of the compressed data member, which is what the
            # verifier recomputes before extracting anything.
            "data_sha256": hashlib.sha256(parts[data_name]).hexdigest(),
            "payload_root": payload_root,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        },
    }

    signature = base64.b64encode(_private_key(args.key).sign(canonical(payload).encode())).decode()
    envelope = {"payload": payload, "key_id": args.key_id, "signature": signature}

    # Exclusive creation: an existing envelope is never overwritten, and the
    # check cannot be raced by another writer between test and open.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(canonical(envelope))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise SystemExit(f"refused: {args.output} exists, choose a new path") from None

    print(
        json.dumps(
            {
                "envelope": str(args.output),
                "release_id": args.release_id,
                "package_version": fields["Version"],
                "versions": versions,
                "sha256": payload["package"]["sha256"],
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
