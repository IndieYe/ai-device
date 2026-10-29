"""Registered-instance activation: the step the update transaction never had.

The signed update path authenticates a package, prepares a release, applies the
Debian package and then stops at ``package_applied_pending_activation``. Nothing
promoted the prepared release. These tests pin the promotion contract: it must
refuse while the launcher holds the shared lease, refuse a candidate prepared
against a different instance state, and move the pointer when allowed.

They also pin what the candidate record may *not* do. That record sits in the
owner's tree, so an owner can write a self-consistent one without ever passing
signature verification. Activation therefore re-verifies the envelope itself and
binds the signed manifest digest to the release it promotes.
"""

import base64
import fcntl
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from device_runtime import native_prepare as np
from device_runtime.protocol import canonical


def _manifest_bytes(release):
    """Stand-in bundle manifest; activation only compares its digest."""
    return json.dumps({"format_version": 3, "release": release}, sort_keys=True).encode()


def _sign(tmp_path, release, manifest_sha256, *, key_id="release-key"):
    """Write an envelope/keys pair and return their paths."""
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    payload = {
        "schema": 1,
        "release_id": release,
        "key_id": key_id,
        "package": {
            "name": "ai-device-native-client",
            "architecture": "amd64",
            "manifest_sha256": manifest_sha256,
        },
    }
    envelope = {
        "payload": payload,
        "key_id": key_id,
        "signature": base64.b64encode(private.sign(canonical(payload).encode())).decode(),
    }
    envelope_path = tmp_path / "envelope.json"
    keys_path = tmp_path / "keys.json"
    envelope_path.write_text(json.dumps(envelope))
    keys_path.write_text(json.dumps({key_id: base64.b64encode(public).decode()}))
    return envelope_path, keys_path


def _prepare_release(root, release="new"):
    """Create the release directory with a manifest and return that digest."""
    directory = root / "releases" / release
    (directory / "bundle").mkdir(parents=True, exist_ok=True)
    (directory / "bundle" / "manifest.json").write_bytes(_manifest_bytes(release))
    return hashlib.sha256(_manifest_bytes(release)).hexdigest()


@pytest.fixture
def instance(tmp_path, monkeypatch):
    root = tmp_path / "instance"
    (root / "releases" / "new").mkdir(parents=True)
    (root / ".installation.lock").touch()
    (root / "instance.json").write_text('{"format_version":1,"instance_id":"probe"}')
    (root / "config").mkdir()
    (root / "config/roles.json").write_text('{"client":{"device_id":"probe"}}')
    (root / "active.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "release": "old",
                "roles": ["client"],
                "release_version": "1.0",
                "bundle_sha256": "0" * 64,
            }
        )
    )
    (root / np.MARKER).write_text("fixture binding")
    _prepare_release(root, "new")

    binding = {"uid": 12345, "root": str(root)}
    active = json.loads((root / "active.json").read_text())
    monkeypatch.setattr(np, "_binding", lambda _: (json.loads(json.dumps(binding)), json.loads(json.dumps(active))))
    monkeypatch.setattr(np, "native_preparation_lease", _noop_lease)
    monkeypatch.setattr(np.helper, "_trusted", lambda *a, **kw: None)

    candidate = {"release": "new", "roles": ["client"]}
    calls = []

    # The promotion reuses lifecycle's private helpers; stub only the pieces that
    # would need a real built release and a real pointer file.
    def check_release(root, release, roles, **kw):
        record = json.loads((root / "releases" / release / "candidate.json").read_text())
        return record["candidate"]

    monkeypatch.setattr(np.lifecycle, "_check_release", check_release)

    def publish(root, checked):
        calls.append((str(root), checked["release"]))
        return {"release": checked["release"], "roles": ["client"]}

    monkeypatch.setattr(np.lifecycle, "_publish", publish)
    return root, binding, active, candidate, calls


def _noop_lease(root):
    from contextlib import nullcontext

    return nullcontext()


def _write_record(root, binding, candidate):
    (root / "releases" / "new" / "candidate.json").write_text(
        json.dumps({"format_version": 1, "binding": binding, "candidate": candidate})
    )


def _activate(root, release, envelope, keys, *, stopped=True):
    return np.activate_candidate(
        root, release, envelope=envelope, trusted_keys=keys, stopped=stopped
    )


def test_activation_requires_stopped_flag(instance, tmp_path):
    root, _binding, active, candidate, calls = instance
    _write_record(root, np.core._lifecycle_binding(root, active), candidate)
    envelope, keys = _sign(tmp_path, "new", hashlib.sha256(_manifest_bytes("new")).hexdigest())

    with pytest.raises(np.PreparationError, match="--stopped"):
        _activate(root, "new", envelope, keys, stopped=False)
    assert calls == []


def test_activation_refused_while_launcher_holds_lease(instance, tmp_path):
    """A running instance holds the installation lease shared; promotion needs it exclusively."""
    root, _binding, active, candidate, calls = instance
    _write_record(root, np.core._lifecycle_binding(root, active), candidate)
    envelope, keys = _sign(tmp_path, "new", hashlib.sha256(_manifest_bytes("new")).hexdigest())

    with (root / ".installation.lock").open("rb") as lease:
        fcntl.flock(lease, fcntl.LOCK_SH)
        with pytest.raises(np.PreparationError, match="running|maintenance"):
            _activate(root, "new", envelope, keys)

    assert calls == []


def test_activation_refused_for_candidate_of_other_instance_state(instance, tmp_path):
    root, binding, _, candidate, calls = instance
    stale = dict(binding, active={"release": "different"})
    _write_record(root, stale, candidate)
    envelope, keys = _sign(tmp_path, "new", hashlib.sha256(_manifest_bytes("new")).hexdigest())

    with pytest.raises(np.PreparationError, match="not prepared for this instance"):
        _activate(root, "new", envelope, keys)
    assert calls == []


def test_activation_promotes_prepared_release(instance, tmp_path):
    root, _binding, active, candidate, calls = instance
    _write_record(root, np.core._lifecycle_binding(root, active), candidate)
    envelope, keys = _sign(tmp_path, "new", hashlib.sha256(_manifest_bytes("new")).hexdigest())

    result = _activate(root, "new", envelope, keys)

    assert result["state"] == "activated"
    assert result["release"] == "new"
    assert calls == [(str(root), "new")]


def test_activation_accepts_dotted_release_names(instance, tmp_path):
    """Release IDs contain dots and plus signs; instance-id rules must not apply."""
    root, _binding, active, _candidate, calls = instance
    release = "0.2.2.post1-a8db1f6f687448c19e88afadbb91cd0d"
    (root / "releases" / release).mkdir()
    _prepare_release(root, release)
    (root / "releases" / release / "candidate.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "binding": np.core._lifecycle_binding(root, active),
                "candidate": {"release": release, "roles": ["client"]},
            }
        )
    )
    envelope, keys = _sign(tmp_path, release, hashlib.sha256(_manifest_bytes(release)).hexdigest())

    result = _activate(root, release, envelope, keys)

    assert result["state"] == "activated"
    assert calls[0][1] == release


def test_activation_does_not_route_through_management_lease(instance, tmp_path, monkeypatch):
    """activate_release takes install._lock, which rejects any marked instance.

    A registered instance always carries the native marker, so reusing the public
    lifecycle entry made every real promotion fail with "requires the native
    lifecycle manager". Pin that this path never calls it.
    """
    root, _binding, active, candidate, _calls = instance
    _write_record(root, np.core._lifecycle_binding(root, active), candidate)
    envelope, keys = _sign(tmp_path, "new", hashlib.sha256(_manifest_bytes("new")).hexdigest())

    def boom(*a, **kw):
        raise AssertionError("activate_release would take the legacy lease")

    monkeypatch.setattr(np.lifecycle, "activate_release", boom)
    assert _activate(root, "new", envelope, keys)["state"] == "activated"


def test_activation_refuses_unsigned_candidate_record(instance, tmp_path):
    """A self-consistent record alone is not authorization.

    This is the reproduced gap: the owner writes candidate.json itself, so the
    record proves nothing. A well-formed record with an envelope signed by a key
    the caller does not trust must never promote.
    """
    root, _binding, active, candidate, calls = instance
    _write_record(root, np.core._lifecycle_binding(root, active), candidate)
    envelope, keys = _sign(tmp_path, "new", hashlib.sha256(_manifest_bytes("new")).hexdigest())

    # Replace the trusted key set with an unrelated key; the signature no longer
    # chains to anything the caller trusts.
    other = Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    keys.write_text(json.dumps({"release-key": base64.b64encode(other).decode()}))

    with pytest.raises(np.candidate.CandidateError, match="signature invalid"):
        _activate(root, "new", envelope, keys)
    assert calls == []


def test_activation_refuses_release_tampered_after_prepare(instance, tmp_path):
    """The signed manifest digest must match the bytes being promoted.

    Preparation verified a package, but the release directory stays writable by
    its owner afterwards. Replacing a wheel and rewriting the record leaves a
    self-consistent release; only the signed digest catches it.
    """
    root, _binding, active, candidate, calls = instance
    _write_record(root, np.core._lifecycle_binding(root, active), candidate)
    envelope, keys = _sign(tmp_path, "new", hashlib.sha256(_manifest_bytes("new")).hexdigest())

    (root / "releases" / "new" / "bundle" / "manifest.json").write_bytes(
        json.dumps({"format_version": 3, "release": "tampered"}, sort_keys=True).encode()
    )

    with pytest.raises(np.PreparationError, match="signed manifest"):
        _activate(root, "new", envelope, keys)
    assert calls == []


def test_activation_requires_signed_package_descriptor(instance, tmp_path):
    """An authenticated payload without a package descriptor cannot bind anything."""
    root, _binding, active, candidate, calls = instance
    _write_record(root, np.core._lifecycle_binding(root, active), candidate)

    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    payload = {"schema": 1, "release_id": "new", "key_id": "release-key"}
    envelope_path = tmp_path / "envelope.json"
    keys_path = tmp_path / "keys.json"
    envelope_path.write_text(
        json.dumps(
            {
                "payload": payload,
                "key_id": "release-key",
                "signature": base64.b64encode(
                    private.sign(canonical(payload).encode())
                ).decode(),
            }
        )
    )
    keys_path.write_text(json.dumps({"release-key": base64.b64encode(public).decode()}))

    with pytest.raises(np.PreparationError, match="package descriptor"):
        _activate(root, "new", envelope_path, keys_path)
    assert calls == []
