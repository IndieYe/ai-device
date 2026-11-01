import hashlib
import json
import runpy
from pathlib import Path

import pytest

module = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts/stage-native-dependencies.py")
)
load_lock, stage, write_constraints = module["load_lock"], module["stage"], module["write_constraints"]

TARGET = {"system": "linux", "machine": "x86_64", "python": "cp313", "glibc": "2.17"}


def make_lock(**overrides):
    entry = {
        "path": "cryptography-50.0.2-cp311-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        "distribution": "cryptography",
        "version": "50.0.2",
        "url": "https://example.invalid/cryptography.whl",
        "sha256": "a" * 64,
        "size": 4,
        "source": "https://pypi.org/project/cryptography/50.0.2/",
        "license": "Apache-2.0 OR BSD-3-Clause",
    }
    lock = {
        "schema_version": 1,
        "target": dict(TARGET),
        "wheels": [dict(entry)],
        "pins": {"cryptography": "50.0.2"},
    }
    for key, value in overrides.items():
        if key in entry:
            lock["wheels"][0][key] = value
        else:
            lock[key] = value
    return lock


def write(tmp_path, lock):
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(lock))
    return path


def test_repository_lock_is_a_valid_delivery_authority():
    """The committed lock must stay self-consistent with its own pins."""
    lock = load_lock(Path(__file__).parents[1] / "native/dependency-lock.json")
    assert len(lock["wheels"]) == 13
    assert lock["target"] == TARGET
    assert set(lock["pins"]) == {w["distribution"] for w in lock["wheels"]}
    # Every native extra dependency must be present, or activation lacks it.
    assert {"cryptography", "zstandard", "cffi", "pycparser"} <= set(lock["pins"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"target": {**TARGET, "glibc": "2.28"}},
        {"wheels": []},
        {"path": "nested/cryptography.whl"},
        {"path": "cryptography.tar.gz"},
        {"sha256": "abc"},
        {"size": 0},
        {"source": " "},
        {"license": ""},
        {"pins": {"cryptography": "49.0.0"}},
    ],
)
def test_lock_rejects_incomplete_or_widened_contract(tmp_path, overrides):
    with pytest.raises(ValueError):
        load_lock(write(tmp_path, make_lock(**overrides)))


def test_stage_requires_explicit_download(tmp_path):
    lock = make_lock()
    with pytest.raises(ValueError, match="explicit --download required"):
        stage(lock, tmp_path / "deps")
    assert not (tmp_path / "deps").exists()


def test_stage_refuses_existing_output(tmp_path):
    (tmp_path / "deps").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        stage(make_lock(), tmp_path / "deps", download=True)


def test_constraints_are_exact_pins_only(tmp_path):
    path = tmp_path / "constraints.txt"
    write_constraints(make_lock(), path)
    assert path.read_text() == "cryptography==50.0.2\n"
    with pytest.raises(ValueError, match="already exist"):
        write_constraints(make_lock(), path)


def test_staged_manifest_carries_provenance_and_matches_bytes(tmp_path, monkeypatch):
    """The directory format build_bundle reads must gain source and license."""
    payload = b"wheel bytes"
    digest = hashlib.sha256(payload).hexdigest()
    lock = make_lock(sha256=digest, size=len(payload), path="cryptography-1-py3-none-any.whl")

    class Response:
        def read(self, _):
            nonlocal payload
            block, payload = payload, b""
            return block

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(module["urllib"].request, "urlopen", lambda url, timeout: Response())
    manifest = stage(lock, tmp_path / "deps", download=True)
    written = json.loads((tmp_path / "deps" / "manifest.json").read_text())
    assert written == manifest
    assert manifest["target"] == TARGET
    assert manifest["wheels"] == [
        {
            "path": "cryptography-1-py3-none-any.whl",
            "sha256": digest,
            "source": "https://pypi.org/project/cryptography/50.0.2/",
            "license": "Apache-2.0 OR BSD-3-Clause",
        }
    ]


DARWIN_TARGET = {"system": "darwin", "machine": "arm64", "python": "cp313", "min_macos": "11.0"}


def test_repository_darwin_lock_is_a_valid_delivery_authority():
    """The macOS lock must be as self-consistent as the Linux one."""
    lock = load_lock(Path(__file__).parents[1] / "native/dependency-lock-darwin-arm64.json")
    assert len(lock["wheels"]) == 13
    assert lock["target"] == DARWIN_TARGET
    assert set(lock["pins"]) == {w["distribution"] for w in lock["wheels"]}
    assert {"cryptography", "zstandard", "cffi", "pycparser"} <= set(lock["pins"])
    # The two locks must pin the same versions, or the macOS bundle would ship
    # a different dependency closure than the Linux bundle at the same release.
    linux = load_lock(Path(__file__).parents[1] / "native/dependency-lock.json")
    assert lock["pins"] == linux["pins"]
    # Every binary wheel must be arm64; a stray x86_64 entry would stage a set
    # that cannot be installed on the target.
    for entry in lock["wheels"]:
        if entry["path"].endswith("-py3-none-any.whl"):
            continue
        assert "arm64" in entry["path"], entry["path"]
        assert "macosx" in entry["path"], entry["path"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"target": {**DARWIN_TARGET, "machine": "x86_64"}},
        {"target": {**DARWIN_TARGET, "min_macos": "10.9"}},
        {"target": {**DARWIN_TARGET, "python": "cp312"}},
    ],
)
def test_darwin_lock_rejects_widened_contract(tmp_path, overrides):
    lock = make_lock(target=overrides["target"])
    lock["wheels"][0]["path"] = "cryptography-50.0.2-cp311-abi3-macosx_11_0_arm64.whl"
    with pytest.raises(ValueError, match="unsupported dependency lock target"):
        load_lock(write(tmp_path, lock))


def test_stage_accepts_darwin_lock(tmp_path):
    lock = make_lock(target=dict(DARWIN_TARGET))
    lock["wheels"][0]["path"] = "cryptography-50.0.2-cp311-abi3-macosx_11_0_arm64.whl"
    with pytest.raises(ValueError, match="explicit --download required"):
        stage(lock, tmp_path / "deps")
