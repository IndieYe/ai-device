"""Structural wheel fixtures do not certify native codec behavior."""

import hashlib
import json
from zipfile import ZipFile

import pytest
from test_bundle import artifacts as artifacts  # noqa: PLC0414

from device_runtime.bundle import BundleError, build_bundle, inspect_bundle
from device_runtime.dependencies import (
    DARWIN_TARGET,
    TARGET,
    dependency_target,
    inspect_dependencies,
)


def dependency_set(directory, pins):
    directory.mkdir()
    wheels = []
    for name, version in pins.items():
        filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
        path = directory / filename
        with ZipFile(path, "w") as archive:
            archive.writestr(
                f"{name}-{version}.dist-info/METADATA",
                f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n",
            )
        wheels.append(
            {
                "path": filename,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source": "https://pypi.org/project/" + name,
                "license": "MIT",
            }
        )
    manifest = {"format_version": 1, "target": TARGET, "wheels": wheels}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_complete_set_and_hash(tmp_path):
    pins = {"pydantic": "2.13.5"}
    directory = tmp_path / "dependencies"
    manifest = dependency_set(directory, pins)
    assert inspect_dependencies(directory, pins) == manifest
    (directory / manifest["wheels"][0]["path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        inspect_dependencies(directory, pins)


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "unlisted", "target", "license", "path"]
)
def test_reject_incomplete_or_wrong_set(tmp_path, change):
    pins = {"pydantic": "2.13.5"}
    directory = tmp_path / "dependencies"
    manifest = dependency_set(directory, pins)
    if change == "missing":
        pins["packaging"] = "26.3"
    elif change == "duplicate":
        manifest["wheels"] *= 2
    elif change == "unlisted":
        (directory / "extra.whl").write_bytes(b"extra")
    elif change == "target":
        manifest["target"] = {**TARGET, "glibc": "2.28"}
    elif change == "license":
        manifest["wheels"][0]["license"] = ""
    else:
        manifest["wheels"][0]["path"] = "../escape.whl"
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        inspect_dependencies(directory, pins)


def test_v3_bundle_snapshot_and_legacy(tmp_path, artifacts):
    dependencies = tmp_path / "dependencies"
    dependency_set(dependencies, {"pydantic": "2.13.5", "websockets": "17.2"})
    output = tmp_path / "v3"
    manifest = build_bundle(output, **artifacts, target_system="linux", dependency_dir=dependencies)
    assert manifest["format_version"] == 3
    assert inspect_bundle(output) == manifest
    (output / "dependencies" / "manifest.json").write_text("{}")
    with pytest.raises(BundleError):
        inspect_bundle(output)
    legacy = build_bundle(tmp_path / "v2", **artifacts, target_system="linux")
    assert legacy["format_version"] == 2
    assert inspect_bundle(tmp_path / "v2") == legacy


@pytest.mark.parametrize(
    "tag",
    [
        "cp313-cp313-manylinux_2_28_x86_64",
        "cp313-cp313-manylinux_2_27_x86_64",
        "cp313-cp313-manylinux_2_18_x86_64",
        "cp312-cp312-manylinux_2_17_x86_64",
        "cp313-cp313-manylinux_2_17_aarch64",
    ],
)
def test_reject_incompatible_native_wheel(tmp_path, tag):
    pins = {"pydantic": "2.13.5"}
    directory = tmp_path / "dependencies"
    manifest = dependency_set(directory, pins)
    entry = manifest["wheels"][0]
    old = directory / entry["path"]
    entry["path"] = f"pydantic-2.13.5-{tag}.whl"
    old.rename(directory / entry["path"])
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="incompatible"):
        inspect_dependencies(directory, pins)


@pytest.mark.parametrize(
    "tag",
    [
        "manylinux2014_x86_64",
        "manylinux_2_17_x86_64",
        "manylinux_2_17_x86_64.manylinux_2_28_x86_64",
    ],
)
def test_accept_native_cp313_compatible_baseline(tmp_path, tag):
    pins = {"pydantic": "2.13.5"}
    directory = tmp_path / "dependencies"
    manifest = dependency_set(directory, pins)
    entry = manifest["wheels"][0]
    old = directory / entry["path"]
    entry["path"] = f"pydantic-2.13.5-cp313-cp313-{tag}.whl"
    old.rename(directory / entry["path"])
    (directory / "manifest.json").write_text(json.dumps(manifest))
    assert inspect_dependencies(directory, pins) == manifest


def test_built_pillow_source_is_self_contained():
    import runpy
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts/manifest-dependencies.py"
    source = runpy.run_path(str(script))["PILLOW_SOURCE"]
    assert "../" not in source
    assert "3b8182a766685eaa002637e28b4ec8d6b18819a0c71f579bf0dbaa5830297cce" in source
    assert "jpegsrc.v9f.tar.gz" in source and "libwebp-1.6.0.tar.gz" in source


def test_previous_glibc_manifest_preserves_its_target(tmp_path):
    pins = {"pydantic": "2.13.5"}
    directory = tmp_path / "dependencies"
    manifest = dependency_set(directory, pins)
    manifest["target"] = {**TARGET, "glibc": "2.27"}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    assert inspect_dependencies(directory, pins)["target"]["glibc"] == "2.27"


@pytest.mark.parametrize("baseline,tag,accepted", [
    ("2.17", "manylinux_2_27_x86_64", False),
    ("2.27", "manylinux_2_27_x86_64", True),
    ("2.27", "manylinux_2_28_x86_64", False),
    ("2.17", "manylinux2014_x86_64", True),
])
def test_tags_follow_declared_target(tmp_path, baseline, tag, accepted):
    pins = {"pydantic": "2.13.5"}
    directory = tmp_path / "deps"
    manifest = dependency_set(directory, pins)
    manifest["target"] = {**TARGET, "glibc": baseline}
    entry = manifest["wheels"][0]
    old = directory / entry["path"]
    entry["path"] = f"pydantic-2.13.5-cp313-cp313-{tag}.whl"
    old.rename(directory / entry["path"])
    (directory / "manifest.json").write_text(json.dumps(manifest))
    if accepted:
        assert inspect_dependencies(directory, pins) == manifest
    else:
        with pytest.raises(ValueError, match="incompatible"):
            inspect_dependencies(directory, pins)


def test_darwin_target_returns_macos_floor():
    assert dependency_target(DARWIN_TARGET) == (11, 0)


@pytest.mark.parametrize(
    "target",
    [
        {"system": "darwin", "machine": "x86_64", "python": "cp313", "min_macos": "11.0"},
        {"system": "darwin", "machine": "arm64", "python": "cp313", "min_macos": "10.9"},
        {"system": "darwin", "machine": "arm64", "python": "cp312", "min_macos": "11.0"},
        {"system": "windows", "machine": "amd64", "python": "cp313"},
    ],
)
def test_darwin_target_rejects_widened_or_foreign_contract(target):
    with pytest.raises(ValueError, match="unsupported"):
        dependency_target(target)


def test_darwin_dependency_set_accepts_arm64_cp313_wheel(tmp_path):
    pins = {"pydantic": "2.13.5"}
    directory = tmp_path / "dependencies"
    manifest = dependency_set(directory, pins)
    manifest["target"] = DARWIN_TARGET
    entry = manifest["wheels"][0]
    old = directory / entry["path"]
    entry["path"] = "pydantic-2.13.5-cp313-cp313-macosx_11_0_arm64.whl"
    old.rename(directory / entry["path"])
    (directory / "manifest.json").write_text(json.dumps(manifest))
    assert inspect_dependencies(directory, pins) == manifest


@pytest.mark.parametrize(
    "tag,accepted",
    [
        # A wheel built for an older macOS runs on a newer host, so the lock's
        # floor is a minimum and every higher tag must be accepted.
        ("macosx_11_0_arm64", True),
        ("macosx_12_0_arm64", True),
        ("macosx_14_0_arm64", True),
        # x86_64 cannot run on an arm64 target, and a floor above the lock's
        # declared minimum would understate what the set requires.
        ("macosx_11_0_x86_64", False),
        ("macosx_10_9_arm64", False),
    ],
)
def test_darwin_tags_follow_declared_floor(tmp_path, tag, accepted):
    pins = {"pydantic": "2.13.5"}
    directory = tmp_path / "deps"
    manifest = dependency_set(directory, pins)
    manifest["target"] = DARWIN_TARGET
    entry = manifest["wheels"][0]
    old = directory / entry["path"]
    entry["path"] = f"pydantic-2.13.5-cp313-cp313-{tag}.whl"
    old.rename(directory / entry["path"])
    (directory / "manifest.json").write_text(json.dumps(manifest))
    if accepted:
        assert inspect_dependencies(directory, pins) == manifest
    else:
        with pytest.raises(ValueError, match="incompatible"):
            inspect_dependencies(directory, pins)


@pytest.mark.parametrize(
    "tag",
    [
        # A cp310/cp311 exact-ABI wheel is not installable on cp313 even though
        # the platform tag matches; only cp313 or an abi3 build may pass.
        "cp310-cp310-macosx_11_0_arm64",
        "cp311-cp311-macosx_11_0_arm64",
    ],
)
def test_darwin_rejects_wrong_cpython_abi(tmp_path, tag):
    pins = {"pydantic": "2.13.5"}
    directory = tmp_path / "deps"
    manifest = dependency_set(directory, pins)
    manifest["target"] = DARWIN_TARGET
    entry = manifest["wheels"][0]
    old = directory / entry["path"]
    entry["path"] = f"pydantic-2.13.5-{tag}.whl"
    old.rename(directory / entry["path"])
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="incompatible"):
        inspect_dependencies(directory, pins)


def test_darwin_accepts_abi3_wheel(tmp_path):
    pins = {"cryptography": "50.0.2"}
    directory = tmp_path / "deps"
    manifest = dependency_set(directory, pins)
    manifest["target"] = DARWIN_TARGET
    entry = manifest["wheels"][0]
    old = directory / entry["path"]
    entry["path"] = "cryptography-50.0.2-cp311-abi3-macosx_11_0_arm64.whl"
    old.rename(directory / entry["path"])
    (directory / "manifest.json").write_text(json.dumps(manifest))
    assert inspect_dependencies(directory, pins) == manifest


def test_darwin_bundle_accepts_local_dependency_set(tmp_path, artifacts):
    dependencies = tmp_path / "dependencies"
    manifest = dependency_set(dependencies, {"pydantic": "2.13.5", "websockets": "17.2"})
    manifest["target"] = DARWIN_TARGET
    (dependencies / "manifest.json").write_text(json.dumps(manifest))
    library = build_bundle(
        tmp_path / "darwin-v3", **artifacts, target_system="darwin", dependency_dir=dependencies
    )
    assert library["format_version"] == 3
    assert library["target"]["system"] == "darwin"
    assert inspect_bundle(tmp_path / "darwin-v3") == library


def test_darwin_bundle_rejects_linux_dependency_set(tmp_path, artifacts):
    dependencies = tmp_path / "dependencies"
    dependency_set(dependencies, {"pydantic": "2.13.5", "websockets": "17.2"})
    with pytest.raises(BundleError, match="must match the bundle target system"):
        build_bundle(
            tmp_path / "mixed", **artifacts, target_system="darwin", dependency_dir=dependencies
        )


def test_linux_bundle_rejects_darwin_dependency_set(tmp_path, artifacts):
    dependencies = tmp_path / "dependencies"
    manifest = dependency_set(dependencies, {"pydantic": "2.13.5", "websockets": "17.2"})
    manifest["target"] = DARWIN_TARGET
    (dependencies / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BundleError, match="must match the bundle target system"):
        build_bundle(
            tmp_path / "mixed", **artifacts, target_system="linux", dependency_dir=dependencies
        )
