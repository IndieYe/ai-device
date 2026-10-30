"""Development-only real-DEB signature/extraction acceptance (Linux private filesystem).

Usage: python accept-native-candidate.py PACKAGE OUTPUT --development-ephemeral-key
Requires the native extra, a new OUTPUT, and ample space. No install or activation.
The generated public trust root is only used in memory for this one acceptance.
"""

import argparse
import base64
import hashlib
import io
import tarfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from zstandard import ZstdDecompressor

from device_runtime.native_candidate import MAX_PACKAGE, _digest, _json, prepare_candidate
from device_runtime.protocol import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("package", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--development-ephemeral-key", required=True, action="store_true")
args = parser.parse_args()
if not 0 < args.package.stat().st_size <= MAX_PACKAGE:
    raise ValueError("package exceeds acceptance budget")
# This acceptance script derives a test descriptor from the known development
# fixture. Production descriptors must come from the external release publisher.
parts = {}
with args.package.open("rb") as stream:
    if stream.read(8) != b"!<arch>\n":
        raise ValueError("not a Debian archive")
    while header := stream.read(60):
        name = header[:16].decode().strip().rstrip("/")
        size = int(header[48:58])
        if not 0 <= size <= MAX_PACKAGE or name in parts:
            raise ValueError("invalid fixture member")
        parts[name] = stream.read(size)
        if size % 2:
            stream.read(1)
with (
    ZstdDecompressor(max_window_size=134217728).stream_reader(
        io.BytesIO(parts["data.tar.zst"])
    ) as stream,
    tarfile.open(fileobj=stream, mode="r|") as archive,
):
    manifest_bytes = None
    for member in archive:
        if member.name.endswith("/bundle/manifest.json"):
            if manifest_bytes is not None or not member.isfile() or member.size > 256000:
                raise ValueError("invalid fixture manifest")
            manifest_bytes = archive.extractfile(member).read()
            payload_root = member.name.removeprefix("./").removesuffix("/bundle/manifest.json")
    if manifest_bytes is None:
        raise ValueError("fixture manifest missing")
manifest = _json(manifest_bytes)
key = Ed25519PrivateKey.generate()
payload = {
    "schema": 1,
    "release_id": "native-candidate-development-only",
    "key_id": "ephemeral-test",
    "versions": {name: value["version"] for name, value in manifest["components"].items()},
    "bootstrap_min": "1",
    "target": "linux-amd64-deb",
    "allowed_sources": ["0.2.2"],
    "required_space": 2_000_000_000,
    "compatibility": {"protocol": [1], "state": [1]},
    "package": {
        "name": "ai-device-native-client",
        "version": "0.2.2-1",
        "architecture": "amd64",
        "size": args.package.stat().st_size,
        "sha256": _digest(args.package),
        "data_sha256": hashlib.sha256(parts["data.tar.zst"]).hexdigest(),
        "payload_root": payload_root,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    },
}
envelope = {
    "payload": payload,
    "key_id": "ephemeral-test",
    "signature": base64.b64encode(key.sign(canonical(payload).encode())).decode(),
}
args.output.mkdir(mode=0o700)
envelope_path = args.output / "development-release.json"
envelope_path.write_text(canonical(envelope))
keys = {"ephemeral-test": base64.b64encode(key.public_key().public_bytes_raw()).decode()}
result = prepare_candidate(
    args.package,
    envelope_path,
    args.output / "candidate",
    trusted_keys=keys,
    release_id=payload["release_id"],
    target=payload["target"],
    source_version="0.2.2",
    bootstrap_version="1",
)
print(canonical({"development_only": True, **result}))
