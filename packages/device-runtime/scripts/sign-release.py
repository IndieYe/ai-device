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
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from device_runtime.native_candidate import MAX_PACKAGE, _json
from device_runtime.protocol import canonical


def _ar_members(path):
    """Yield (name, bytes) for a Debian ar archive without spawning ar."""
    parts = {}
    with path.open("rb") as stream:
        if stream.read(8) != b"!<arch>\n":
            raise SystemExit("not a Debian archive: %s" % path)
        while header := stream.read(60):
            if len(header) < 60:
                raise SystemExit("truncated ar header")
            name = header[:16].decode().strip().rstrip("/")
            size = int(header[48:58].decode().strip())
            if not 0 <= size <= MAX_PACKAGE or name in parts:
                raise SystemExit("invalid ar member: %s" % name)
            parts[name] = stream.read(size)
            if size % 2:
                stream.read(1)
    return parts


def _control(data_name, body):
    """Return the control fields plus the compressed data digest."""
    suffix = data_name.rsplit(".", 1)[-1]
    if suffix == "zst":
        from zstandard import ZstdDecompressor

        reader = ZstdDecompressor(max_window_size=134217728).stream_reader(io.BytesIO(body))
        archive = tarfile.open(fileobj=reader, mode="r|")
    elif suffix == "xz":
        import lzma

        archive = tarfile.open(fileobj=io.BytesIO(lzma.decompress(body)), mode="r:")
    elif suffix == "gz":
        import gzip

        archive = tarfile.open(fileobj=io.BytesIO(gzip.decompress(body)), mode="r:")
    else:
        archive = tarfile.open(fileobj=io.BytesIO(body), mode="r:")
    fields = {}
    try:
        with archive:
            for member in archive:
                if member.name.lstrip("./") == "control":
                    text = archive.extractfile(member).read().decode("utf-8")
                    for line in text.splitlines():
                        if line and not line.startswith((" ", "\t")) and ":" in line:
                            key, value = line.split(":", 1)
                            fields[key] = value.strip()
    finally:
        pass
    return fields


def _manifest(data_name, body):
    """Read bundle/manifest.json out of the payload archive inside data.tar."""
    # data.tar holds opt/ai-device/payloads/<tree>.tar, which in turn holds
    # bundle/manifest.json. Both layers are opened read-only and bounded.
    suffix = data_name.rsplit(".", 1)[-1]
    if suffix == "zst":
        from zstandard import ZstdDecompressor

        reader = ZstdDecompressor(max_window_size=134217728).stream_reader(io.BytesIO(body))
        outer = tarfile.open(fileobj=reader, mode="r|")
    elif suffix == "xz":
        import lzma

        outer = tarfile.open(fileobj=io.BytesIO(lzma.decompress(body)), mode="r:")
    elif suffix == "gz":
        import gzip

        outer = tarfile.open(fileobj=io.BytesIO(gzip.decompress(body)), mode="r:")
    else:
        outer = tarfile.open(fileobj=io.BytesIO(body), mode="r:")

    found = None
    payload_root = None
    with outer:
        for member in outer:
            name = member.name[2:] if member.name.startswith("./") else member.name
            # The payload archive is the tree being signed, e.g.
            # opt/ai-device/payloads/<tree>.tar. Only that member can carry the
            # manifest this release refers to.
            if not name.startswith("opt/ai-device/payloads/") or not name.endswith(".tar"):
                continue
            if member.size > MAX_PACKAGE:
                raise SystemExit("payload archive exceeds budget")
            body = outer.extractfile(member).read()
            with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as inner:
                for item in inner:
                    inner_name = item.name[2:] if item.name.startswith("./") else item.name
                    if inner_name != "bundle/manifest.json":
                        continue
                    if found is not None or not item.isfile() or item.size > 256000:
                        raise SystemExit("duplicate or oversized manifest")
                    found = inner.extractfile(item).read()
                    payload_root = name
    if found is None:
        raise SystemExit("bundle/manifest.json not found in payload")
    return found, payload_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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
    if args.output.exists():
        raise SystemExit("refused: %s exists, choose a new path" % args.output)

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
            "refused: package ships device %s but --source-version is %s"
            % (versions.get("device"), args.source_version)
        )

    payload = {
        "schema": 1,
        "release_id": args.release_id,
        "key_id": args.key_id,
        "versions": versions,
        "bootstrap_min": args.bootstrap_min or args.source_version,
        "target": "linux-amd64-deb",
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

    private = serialization.load_pem_private_key(args.key.read_bytes(), password=None)
    signature = base64.b64encode(private.sign(canonical(payload).encode())).decode()
    envelope = {"payload": payload, "key_id": args.key_id, "signature": signature}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical(envelope), encoding="utf-8")
    print(json.dumps({
        "envelope": str(args.output),
        "release_id": args.release_id,
        "package_version": fields["Version"],
        "versions": versions,
        "sha256": payload["package"]["sha256"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
