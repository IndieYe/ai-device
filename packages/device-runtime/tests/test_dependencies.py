"""Structural wheel fixtures do not certify native codec behavior."""

import hashlib
import json
from zipfile import ZipFile

import pytest
from test_bundle import artifacts as artifacts  # noqa: PLC0414

from device_runtime.bundle import BundleError, build_bundle, inspect_bundle
from device_runtime.dependencies import TARGET, inspect_dependencies


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


def test_accept_native_cp313_manylinux2014(tmp_path):
    pins = {"pydantic": "2.13.5"}
    directory = tmp_path / "dependencies"
    manifest = dependency_set(directory, pins)
    entry = manifest["wheels"][0]
    old = directory / entry["path"]
    entry["path"] = "pydantic-2.13.5-cp313-cp313-manylinux2014_x86_64.whl"
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
