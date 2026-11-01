"""The signer must bind exactly what it read, and refuse what it cannot vouch for.

These build a real ar/tar package in memory rather than mocking the parser: the
bugs this file guards against were in ar member layout and in the payload root
path, both of which a mocked reader would have hidden.
"""

import io
import json
import runpy
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from device_runtime.native_candidate import CandidateError, verify_release

module = runpy.run_path(str(Path(__file__).parents[1] / "scripts/sign-release.py"))
_manifest = module["_manifest"]
_control = module["_control"]
_ar_members = module["_ar_members"]
_private_key = module["_private_key"]
main = module["main"]

TREE = "0.2.4-native1"
MANIFEST = {
    "format_version": 3,
    "target": {"system": "linux", "machine": "x86_64"},
    "components": {
        "device": {"version": "0.2.4"},
        "bash": {"version": "0.2.0"},
        "fs": {"version": "0.2.0"},
    },
}
CONTROL = b"Package: ai-device-native-client\nVersion: 0.2.4-1\nArchitecture: amd64\n"


def _tar(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, body in entries:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def _deb(data_members, control=CONTROL):
    """Assemble a minimal but structurally real .deb."""
    data = _tar(data_members)
    control_tar = _tar([("./control", control)])
    joined = b"".join(
        [b"!<arch>\n"]
        + [
            f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(body):<10}`\n".encode()
            + body
            + (b"\n" if len(body) % 2 else b"")
            for name, body in [
                ("debian-binary", b"2.0\n"),
                ("control.tar", control_tar),
                ("data.tar", data),
            ]
        ]
    )
    return joined


def _member(deb_bytes, name):
    """Extract one ar member, which is what the signer is handed."""
    return _ar_members_from_bytes(deb_bytes)[name]


def _ar_members_from_bytes(deb_bytes):
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".deb") as handle:
        handle.write(deb_bytes)
        handle.flush()
        return _ar_members(Path(handle.name))


def _payload_entries(bootstrap=True):
    inner = _tar([("./bundle/manifest.json", json.dumps(MANIFEST).encode())])
    entries = [
        (f"./opt/ai-device/payloads/{TREE}.tar", inner),
        ("./usr/bin/ai-device-native-install", b"#!/bin/sh\n"),
    ]
    if bootstrap:
        # The bootstrap archive has no bundle; only one member may name itself
        # the payload root, and it is the one carrying the manifest.
        bootstrap_tar = _tar([("./run", b"x")])
        entries.insert(1, (f"./opt/ai-device/payloads/{TREE}-bootstrap.tar", bootstrap_tar))
    return entries


def test_payload_root_names_the_member_that_carries_the_manifest():
    found, root = _manifest("data.tar", _member(_deb(_payload_entries()), "data.tar"))
    assert json.loads(found) == MANIFEST
    assert root == f"opt/ai-device/payloads/{TREE}.tar"


def test_two_manifests_are_refused():
    inner = _tar([("./bundle/manifest.json", json.dumps(MANIFEST).encode())])
    members = _payload_entries() + [("./opt/ai-device/payloads/other.tar", inner)]
    with pytest.raises(SystemExit, match="duplicate"):
        _manifest("data.tar", _member(_deb(members), "data.tar"))


def test_package_without_a_manifest_is_refused():
    members = [("./usr/bin/ai-device-native-install", b"#!/bin/sh\n")]
    with pytest.raises(SystemExit, match="not found"):
        _manifest("data.tar", _member(_deb(members), "data.tar"))


def test_control_requires_the_identity_fields():
    with pytest.raises(SystemExit, match="Architecture"):
        _control("control.tar", _tar([("./control", b"Package: x\nVersion: 1\n")]))


def test_control_reads_a_zstd_member():
    """dpkg on newer releases emits ``control.tar.zst``; that path must work.

    The zstd branch streams the tar through a decompression reader. Wrapping
    that reader in ``contextlib.closing`` made the teardown call a ``close``
    attribute the wrapper does not expose, so every zstd package failed to sign
    while the xz packages in earlier builds hid it.
    """
    zstandard = pytest.importorskip("zstandard")
    raw = _tar([("./control", CONTROL)])
    body = zstandard.ZstdCompressor().compress(raw)
    fields = _control("control.tar.zst", body)
    assert fields["Package"] == "ai-device-native-client"
    assert fields["Version"] == "0.2.4-1"
    assert fields["Architecture"] == "amd64"


def test_payload_root_reads_a_zstd_member():
    """The manifest lookup takes the same zstd path, so both are covered."""
    zstandard = pytest.importorskip("zstandard")
    inner = _tar([("./bundle/manifest.json", json.dumps(MANIFEST).encode())])
    entries = [
        (f"./opt/ai-device/payloads/{TREE}.tar", inner),
        (f"./opt/ai-device/payloads/{TREE}-bootstrap.tar", _tar([("./run", b"x")])),
    ]
    body = zstandard.ZstdCompressor().compress(_tar(entries))
    found, root = _manifest("data.tar.zst", body)
    assert json.loads(found) == MANIFEST
    assert root == f"opt/ai-device/payloads/{TREE}.tar"


def test_ar_members_are_read_without_a_system_ar(tmp_path):
    path = tmp_path / "pkg.deb"
    path.write_bytes(_deb(_payload_entries()))
    parts = _ar_members(path)
    assert set(parts) == {"debian-binary", "control.tar", "data.tar"}


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_private_key_must_not_be_readable_beyond_its_owner(tmp_path, mode):
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(mode)
    with pytest.raises(SystemExit, match="readable beyond its owner"):
        _private_key(key_path)


def test_private_key_must_be_an_ed25519_key(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import rsa

    key_path = tmp_path / "rsa.pem"
    key_path.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    with pytest.raises(SystemExit, match="not an Ed25519"):
        _private_key(key_path)


def test_signed_envelope_binds_the_package_bytes(tmp_path, monkeypatch):
    """End to end: sign a real package, then prove a byte change breaks it."""
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    package = tmp_path / "pkg.deb"
    package.write_bytes(_deb(_payload_entries()))
    output = tmp_path / "release.json"

    monkeypatch.setattr(
        "sys.argv",
        [
            "sign-release.py",
            "--package",
            str(package),
            "--key",
            str(key_path),
            "--key-id",
            "test",
            "--release-id",
            "rel-test",
            "--allowed-source",
            "0.2.2",
            "--allowed-source",
            "0.2.4",
            "--source-version",
            "0.2.4",
            "--output",
            str(output),
        ],
    )
    assert main() == 0

    trusted = {
        "test": __import__("base64")
        .b64encode(
            key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
        .decode()
    }
    envelope = json.loads(output.read_text())
    payload = verify_release(
        envelope,
        trusted,
        release_id="rel-test",
        target="linux-amd64-deb",
        source_version="0.2.4",
        bootstrap_version="0.2.4",
    )
    assert payload["package"]["version"] == "0.2.4-1"
    assert payload["package"]["size"] == package.stat().st_size

    tampered = json.loads(json.dumps(envelope))
    tampered["payload"]["package"]["size"] += 1
    with pytest.raises(CandidateError):
        verify_release(
            tampered,
            trusted,
            release_id="rel-test",
            target="linux-amd64-deb",
            source_version="0.2.4",
            bootstrap_version="0.2.4",
        )


def test_existing_envelope_is_never_overwritten(tmp_path, monkeypatch):
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    package = tmp_path / "pkg.deb"
    package.write_bytes(_deb(_payload_entries()))
    output = tmp_path / "release.json"
    output.write_text("{}")
    monkeypatch.setattr(
        "sys.argv",
        [
            "sign-release.py",
            "--package",
            str(package),
            "--key",
            str(key_path),
            "--key-id",
            "test",
            "--release-id",
            "rel-test",
            "--allowed-source",
            "0.2.2",
            "--allowed-source",
            "0.2.4",
            "--source-version",
            "0.2.4",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit, match="exists"):
        main()
    assert output.read_text() == "{}"


def test_shipped_version_must_match_the_package(tmp_path, monkeypatch):
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    package = tmp_path / "pkg.deb"
    package.write_bytes(_deb(_payload_entries()))
    monkeypatch.setattr(
        "sys.argv",
        [
            "sign-release.py",
            "--package",
            str(package),
            "--key",
            str(key_path),
            "--key-id",
            "test",
            "--release-id",
            "rel-test",
            "--allowed-source",
            "0.2.2",
            "--source-version",
            "0.2.3",
            "--output",
            str(tmp_path / "release.json"),
        ],
    )
    with pytest.raises(SystemExit, match="ships device 0.2.4"):
        main()
