"""Registered-instance activation: the step the update transaction never had.

The signed update path authenticates a package, prepares a release, applies the
Debian package and then stops at ``package_applied_pending_activation``. Nothing
promoted the prepared release. These tests pin the promotion contract: it must
refuse while the launcher holds the shared lease, refuse a candidate prepared
against a different instance state, and move the pointer when allowed.
"""

import fcntl
import json

import pytest

from device_runtime import native_prepare as np


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

    binding = {"uid": 12345, "root": str(root)}
    active = json.loads((root / "active.json").read_text())
    monkeypatch.setattr(np, "_binding", lambda _: (json.loads(json.dumps(binding)), json.loads(json.dumps(active))))
    monkeypatch.setattr(np, "native_preparation_lease", _noop_lease)
    monkeypatch.setattr(np.helper, "_trusted", lambda *a, **kw: None)

    candidate = {"release": "new", "roles": ["client"]}
    calls = []

    def activate_release(root, release, *, stopped=False):
        calls.append((str(root), release, stopped))
        return {"release": release, "roles": ["client"]}

    # Keep the real module for _release_id; only the promotion itself is faked.
    monkeypatch.setattr(np.lifecycle, "activate_release", activate_release)
    return root, binding, active, candidate, calls


def _noop_lease(root):
    from contextlib import nullcontext

    return nullcontext()


def _write_record(root, binding, candidate):
    (root / "releases" / "new" / "candidate.json").write_text(
        json.dumps({"format_version": 1, "binding": binding, "candidate": candidate})
    )


def test_activation_requires_stopped_flag(instance):
    root, _binding, active, candidate, calls = instance
    _write_record(root, np.core._lifecycle_binding(root, active), candidate)

    with pytest.raises(np.PreparationError, match="--stopped"):
        np.activate_candidate(root, "new", stopped=False)
    assert calls == []


def test_activation_refused_while_launcher_holds_lease(instance):
    """A running instance holds the installation lease shared; promotion needs it exclusively."""
    root, _binding, active, candidate, calls = instance
    _write_record(root, np.core._lifecycle_binding(root, active), candidate)

    with (root / ".installation.lock").open("rb") as lease:
        fcntl.flock(lease, fcntl.LOCK_SH)
        with pytest.raises(np.PreparationError, match="running|maintenance"):
            np.activate_candidate(root, "new", stopped=True)

    assert calls == []


def test_activation_refused_for_candidate_of_other_instance_state(instance):
    root, binding, _, candidate, calls = instance
    stale = dict(binding, active={"release": "different"})
    _write_record(root, stale, candidate)

    with pytest.raises(np.PreparationError, match="not prepared for this instance"):
        np.activate_candidate(root, "new", stopped=True)
    assert calls == []


def test_activation_promotes_prepared_release(instance):
    root, _binding, active, candidate, calls = instance
    _write_record(root, np.core._lifecycle_binding(root, active), candidate)

    result = np.activate_candidate(root, "new", stopped=True)

    assert result["state"] == "activated"
    assert result["release"] == "new"
    assert calls == [(str(root), "new", True)]


def test_activation_accepts_dotted_release_names(instance):
    """Release IDs contain dots and plus signs; instance-id rules must not apply."""
    root, _binding, active, _candidate, calls = instance
    release = "0.2.2.post1-a8db1f6f687448c19e88afadbb91cd0d"
    (root / "releases" / release).mkdir()
    (root / "releases" / release / "candidate.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "binding": np.core._lifecycle_binding(root, active),
                "candidate": {"release": release, "roles": ["client"]},
            }
        )
    )

    result = np.activate_candidate(root, release, stopped=True)

    assert result["state"] == "activated"
    assert calls[0][1] == release
