import base64
import copy
import hashlib
import io
import json
import tarfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from device_runtime import native_candidate as nc
from device_runtime.protocol import canonical


def tar_bytes(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data, kind in entries:
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if kind == "dir" else 0o644
            if kind == "dir":
                member.type = tarfile.DIRTYPE
            elif kind in {"symlink", "hardlink"}:
                member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                member.linkname = data
            elif kind == "fifo":
                member.type = tarfile.FIFOTYPE
            else:
                member.size = len(data)
            archive.addfile(member, io.BytesIO(data) if kind == "file" else None)
    return output.getvalue()


def deb_bytes(data, control=None):
    control = control or tar_bytes(
        [
            (
                "./control",
                b"Package: ai-device-native-client\nVersion: 2-1\nArchitecture: amd64\n",
                "file",
            ),
            ("./postinst", b"#!/bin/sh\nexit 99\n", "file"),
        ]
    )
    output = b"!<arch>\n"
    for name, value in [
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", control),
        ("data.tar.gz", data),
    ]:
        header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(value):<10}`\n".encode()
        assert len(header) == 60
        output += header + value + (b"\n" if len(value) % 2 else b"")
    return output


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    keys = {"test": base64.b64encode(private.public_key().public_bytes_raw()).decode()}
    root = "opt/ai-device/packages/2-native1"
    data = tar_bytes(
        [
            (root + "/bundle/manifest.json", b"{}", "file"),
            (root + "/runtime/bin/python", b"fixture only", "file"),
        ]
    )
    package = tmp_path / "input.deb"
    package.write_bytes(deb_bytes(data))
    payload = {
        "schema": 1,
        "release_id": "release-2",
        "key_id": "test",
        "versions": {"device": "2.0", "bash": "2.0", "fs": "2.0"},
        "bootstrap_min": "1.0",
        "target": "linux-amd64-deb",
        "allowed_sources": ["1.0"],
        "required_space": 1000000,
        "compatibility": {"protocol": [1], "state": [1]},
        "package": {
            "name": "ai-device-native-client",
            "version": "2-1",
            "architecture": "amd64",
            "size": package.stat().st_size,
            "sha256": nc._digest(package),
            "data_sha256": hashlib.sha256(data).hexdigest(),
            "payload_root": root,
            "manifest_sha256": hashlib.sha256(b"{}").hexdigest(),
        },
    }
    envelope_path = tmp_path / "release.json"

    def sign(value=payload):
        envelope = {
            "payload": value,
            "key_id": "test",
            "signature": base64.b64encode(private.sign(canonical(value).encode())).decode(),
        }
        envelope_path.write_text(canonical(envelope))
        return envelope

    sign()
    monkeypatch.setattr(
        nc,
        "inspect_bundle",
        lambda _: {
            "format_version": 3,
            "target": {"system": "linux"},
            "components": {name: {"version": "2.0"} for name in ("device", "bash", "fs")},
        },
    )
    return package, envelope_path, tmp_path / "candidate", keys, payload, sign


def prepare(candidate, **overrides):
    package, envelope, destination, keys, _, _ = candidate
    options = {
        "trusted_keys": keys,
        "release_id": "release-2",
        "target": "linux-amd64-deb",
        "source_version": "1.0",
        "bootstrap_version": "1.0",
    }
    options.update(overrides)
    return nc.prepare_candidate(package, envelope, destination, **options)


def test_authenticated_extract_never_installs_or_activates(candidate):
    result = prepare(candidate)
    assert result["state"] == "authenticated_extracted"
    assert result["activated"] is False and result["venv_prepared"] is False
    destination = candidate[2]
    assert (destination / "candidate.json").is_file()
    assert not list(destination.rglob("postinst"))
    with pytest.raises(nc.CandidateError, match="already exists"):
        prepare(candidate)


@pytest.mark.parametrize("change", ["signature", "payload", "key"])
def test_signature_or_trust_tampering_refused(candidate, change):
    _, path, destination, _, _, _ = candidate
    envelope = json.loads(path.read_text())
    if change == "signature":
        envelope["signature"] = base64.b64encode(b"x" * 64).decode()
    elif change == "payload":
        envelope["payload"]["release_id"] = "changed"
    else:
        envelope["key_id"] = "untrusted"
    path.write_text(canonical(envelope))
    with pytest.raises(nc.CandidateError):
        prepare(candidate)
    assert not destination.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("release_id", "other"),
        ("source_version", "other"),
        ("bootstrap_version", "0.1"),
        ("target", "linux-arm64-deb"),
    ],
)
def test_release_context_refused(candidate, field, value):
    with pytest.raises(nc.CandidateError):
        prepare(candidate, **{field: value})
    assert not candidate[2].exists()


def test_package_tamper_cleaned(candidate):
    package = candidate[0]
    data = bytearray(package.read_bytes())
    data[-3] ^= 1
    package.write_bytes(data)
    with pytest.raises(nc.CandidateError, match="digest"):
        prepare(candidate)
    assert not candidate[2].exists()


def test_manifest_tamper_in_signed_package_refused(candidate):
    payload = copy.deepcopy(candidate[4])
    payload["package"]["manifest_sha256"] = "0" * 64
    candidate[5](payload)
    with pytest.raises(nc.CandidateError, match="manifest"):
        prepare(candidate)
    assert not candidate[2].exists()


def test_envelope_read_is_bounded_and_accepts_exact_limit(tmp_path):
    envelope = tmp_path / "release.json"
    envelope.write_bytes(b"{}" + b" " * (nc.MAX_ENVELOPE - 2))
    assert nc._read_envelope(envelope) == {}
    with envelope.open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(nc.CandidateError, match="envelope too large"):
        nc._read_envelope(envelope)


def test_oversized_envelope_refused_before_candidate_creation(candidate):
    candidate[1].write_bytes(b"{}" + b" " * nc.MAX_ENVELOPE)
    with pytest.raises(nc.CandidateError, match="envelope too large"):
        prepare(candidate)
    assert not candidate[2].exists()


def test_duplicate_json_refused(candidate):
    candidate[1].write_text('{"key_id":"test","key_id":"test"}')
    with pytest.raises(nc.CandidateError, match="duplicate"):
        prepare(candidate)


@pytest.mark.parametrize(
    "entries",
    [
        [("../escape", b"bad", "file")],
        [("/absolute", b"bad", "file")],
        [("a", b"one", "file"), ("./a", b"two", "file")],
        [("a", "../../escape", "symlink")],
        [("a", "b", "symlink"), ("b", "../../escape", "symlink")],
        [("a", "b", "symlink"), ("b", "a", "symlink")],
        [("a", "missing", "symlink")],
        [("a", b"", "fifo")],
        [("a", "missing", "hardlink")],
        [("a", "b", "symlink"), ("a/child", b"x", "file")],
    ],
)
def test_unsafe_archive_refused(tmp_path, entries):
    archive = tmp_path / "data.tar"
    archive.write_bytes(tar_bytes(entries))
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises((nc.CandidateError, OSError)):
        nc._extract(archive, root)
    assert not (tmp_path / "escape").exists()


def test_safe_relative_and_hard_links(tmp_path):
    archive = tmp_path / "data.tar"
    archive.write_bytes(
        tar_bytes(
            [
                ("a/file", b"ok", "file"),
                ("a/link", "file", "symlink"),
                ("hard", "a/file", "hardlink"),
            ]
        )
    )
    root = tmp_path / "root"
    root.mkdir()
    nc._extract(archive, root)
    assert (root / "a/link").read_bytes() == b"ok"
    assert (root / "hard").read_bytes() == b"ok"


def test_extraction_budget(tmp_path, monkeypatch):
    archive = tmp_path / "data.tar"
    archive.write_bytes(tar_bytes([("file", b"123", "file")]))
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(nc, "MAX_EXPANDED", 2)
    with pytest.raises(nc.CandidateError, match="budget"):
        nc._extract(archive, root)


def test_control_identity_must_match_signed_descriptor(candidate):
    package, _, _, _, payload, sign = candidate
    data_path = package.parent / "data.tar"
    nc._deb_data(package, data_path, payload["package"]["data_sha256"], payload["package"])
    control = tar_bytes(
        [("control", b"Package: other\nVersion: 2-1\nArchitecture: amd64\n", "file")]
    )
    package.write_bytes(deb_bytes(data_path.read_bytes(), control))
    payload["package"].update(size=package.stat().st_size, sha256=nc._digest(package))
    sign()
    with pytest.raises(nc.CandidateError, match="control differs"):
        prepare(candidate)
    assert not candidate[2].exists()


def test_insufficient_space_precedes_creation(candidate, monkeypatch):
    monkeypatch.setattr(nc.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 0})())
    with pytest.raises(nc.CandidateError, match="space"):
        prepare(candidate)
    assert not candidate[2].exists()


@pytest.mark.parametrize("over_budget", [False, True])
def test_zstandard_debian_members(candidate, monkeypatch, over_budget):
    from zstandard import ZstdCompressor

    package, _, _, _, payload, sign = candidate
    data = tar_bytes(
        [(payload["package"]["payload_root"] + "/bundle/manifest.json", b"{}", "file")]
    )
    # Convert gzip tar fixtures to zstd, matching actual dpkg-deb output.
    import gzip

    raw = deb_bytes(data)
    parts = []
    stream = io.BytesIO(raw)
    stream.read(8)
    while header := stream.read(60):
        name = header[:16].decode().strip().rstrip("/")
        size = int(header[48:58])
        value = stream.read(size)
        if size % 2:
            stream.read(1)
        if name.endswith(".gz"):
            name = name.removesuffix(".gz") + ".zst"
            value = ZstdCompressor().compress(gzip.decompress(value))
        parts.append((name, value))
    output = b"!<arch>\n"
    for name, value in parts:
        output += (
            f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(value):<10}`\n".encode()
            + value
            + (b"\n" if len(value) % 2 else b"")
        )
        if name.startswith("data."):
            payload["package"]["data_sha256"] = hashlib.sha256(value).hexdigest()
    package.write_bytes(output)
    payload["package"].update(size=len(output), sha256=nc._digest(package))
    sign()
    if over_budget:
        monkeypatch.setattr(nc, "MAX_EXPANDED", 1024)
        with pytest.raises(nc.CandidateError, match="budget"):
            prepare(candidate)
        assert not candidate[2].exists()
    else:
        assert prepare(candidate)["state"] == "authenticated_extracted"


def test_completion_receipt_failure_cleans_candidate(candidate, monkeypatch):
    real_link = nc.os.link

    def fail(source, target):
        if str(target).endswith("candidate.json"):
            raise OSError("injected completion failure")
        return real_link(source, target)

    monkeypatch.setattr(nc.os, "link", fail)
    with pytest.raises(OSError, match="completion failure"):
        prepare(candidate)
    assert not candidate[2].exists()


def test_existing_destination_never_removed(candidate):
    destination = candidate[2]
    destination.mkdir()
    (destination / "user-file").write_text("keep")
    with pytest.raises(nc.CandidateError, match="exists"):
        prepare(candidate)
    assert (destination / "user-file").read_text() == "keep"


def test_hardlink_via_symbolic_parent_refused(tmp_path):
    archive = tmp_path / "data.tar"
    archive.write_bytes(
        tar_bytes(
            [
                ("dir/file", b"ok", "file"),
                ("alias", "dir", "symlink"),
                ("hard", "alias/file", "hardlink"),
            ]
        )
    )
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(nc.CandidateError, match="hardlink"):
        nc._extract(archive, root)


def test_symlink_0777_is_not_regular_file_write_permission(tmp_path):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        file = tarfile.TarInfo("file")
        file.mode, file.size = 0o644, 2
        archive.addfile(file, io.BytesIO(b"ok"))
        link = tarfile.TarInfo("link")
        link.mode, link.type, link.linkname = 0o777, tarfile.SYMTYPE, "file"
        archive.addfile(link)
    path = tmp_path / "data.tar"
    path.write_bytes(output.getvalue())
    root = tmp_path / "root"
    root.mkdir()
    nc._extract(path, root)
    assert (root / "link").read_bytes() == b"ok"


@pytest.mark.parametrize("mode", [0o666, 0o4755, 0o2755])
def test_writable_or_privileged_regular_file_refused(tmp_path, mode):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        file = tarfile.TarInfo("file")
        file.mode = mode
        archive.addfile(file, io.BytesIO(b""))
    path = tmp_path / "data.tar"
    path.write_bytes(output.getvalue())
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(nc.CandidateError, match="permissions"):
        nc._extract(path, root)


@pytest.mark.parametrize(
    "field,value",
    [
        ("compatibility", {"protocol": [2], "state": [1]}),
        ("compatibility", {"protocol": [True], "state": [1]}),
        ("schema", True),
        ("required_space", 0),
    ],
)
def test_signed_but_unsupported_release_refused(candidate, field, value):
    payload = copy.deepcopy(candidate[4])
    payload[field] = value
    candidate[5](payload)
    with pytest.raises(nc.CandidateError):
        prepare(candidate)
    assert not candidate[2].exists()


def test_signed_data_archive_digest_must_match(candidate):
    payload = copy.deepcopy(candidate[4])
    payload["package"]["data_sha256"] = "0" * 64
    candidate[5](payload)
    with pytest.raises(nc.CandidateError, match="data archive digest"):
        prepare(candidate)
    assert not candidate[2].exists()


def test_ar_member_truncation_refused(candidate):
    package = candidate[0]
    package.write_bytes(package.read_bytes()[:-100])
    payload = candidate[4]
    payload["package"].update(size=package.stat().st_size, sha256=nc._digest(package))
    candidate[5]()
    with pytest.raises(nc.CandidateError, match="truncated"):
        prepare(candidate)
    assert not candidate[2].exists()
