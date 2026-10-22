import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from device_runtime.bundle import COMPONENTS, BundleError, build_bundle, inspect_bundle


def make_wheel(directory, component, *, name=None, version="0.2.0", missing=None, info_dir=None):
    distribution, entries = COMPONENTS[component]
    name = name or distribution
    normalized = name.replace("-", "_")
    path = directory / f"{normalized}-{version}-py3-none-any.whl"
    info = (info_dir or f"{normalized}-{version}.dist-info") + "/"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            info + "METADATA", f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n"
        )
        archive.writestr(
            info + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        for entry in entries:
            if entry != missing:
                archive.writestr(entry, "# structural fixture, not a behavioral core test\n")
    return path


@pytest.fixture
def artifacts(tmp_path):
    wheels = {component: make_wheel(tmp_path, component) for component in COMPONENTS}
    constraints = tmp_path / "input-constraints.txt"
    constraints.write_text("# online third-party pins\npydantic==2.13.5\nwebsockets==17.2\n")
    return {
        "release_version": "0.2.0.dev1",
        "wheels": wheels,
        "constraints": constraints,
        "sources": {component: str(i) * 40 for i, component in enumerate(COMPONENTS, start=1)},
    }


def test_build_and_inspect_exact_snapshot(tmp_path, artifacts):
    output = tmp_path / "release"
    built = build_bundle(output, **artifacts)
    assert inspect_bundle(output) == built
    assert built["roles"]["center"]["components"] == ["device"]
    assert built["roles"]["client"]["components"] == ["device", "bash", "fs"]
    assert built["target"] == {"system": "darwin", "requires_python": ">=3.13", "offline": False}
    for component, original in artifacts["wheels"].items():
        assert (
            output / built["components"][component]["path"]
        ).read_bytes() == original.read_bytes()
        assert built["components"][component]["source_commit"] == artifacts["sources"][component]
    assert not list(tmp_path.glob(".device-bundle-*"))
    # Input files are not mutable dependencies of a built bundle.
    artifacts["constraints"].write_text("changed after build\n")
    artifacts["wheels"]["bash"].unlink()
    assert inspect_bundle(output) == built


def test_returned_manifest_does_not_mutate_contract(tmp_path, artifacts):
    output = tmp_path / "release"
    built = build_bundle(output, **artifacts)
    original = inspect_bundle(output)
    built["roles"]["client"]["components"].append("other")
    built["target"]["system"] = "other"
    assert inspect_bundle(output) == original


def test_existing_directory_is_never_overwritten(tmp_path, artifacts):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep"
    marker.write_text("existing content")
    with pytest.raises(BundleError, match="already exists"):
        build_bundle(output, **artifacts)
    assert marker.read_text() == "existing content"
    assert set(output.iterdir()) == {marker}


@pytest.mark.parametrize("component", COMPONENTS)
def test_wrong_distribution_rejected_without_partial_output(tmp_path, artifacts, component):
    artifacts["wheels"][component] = make_wheel(tmp_path, component, name="unrelated-distribution")
    output = tmp_path / "bad"
    with pytest.raises(BundleError, match="wrong distribution"):
        build_bundle(output, **artifacts)
    assert not output.exists() and not list(tmp_path.glob(".device-bundle-*"))


@pytest.mark.parametrize("component", COMPONENTS)
def test_required_entry_rejected(tmp_path, artifacts, component):
    artifacts["wheels"][component] = make_wheel(
        tmp_path, component, missing=COMPONENTS[component][1][0]
    )
    with pytest.raises(BundleError, match="entry missing"):
        build_bundle(tmp_path / "bad", **artifacts)
    assert not (tmp_path / "bad").exists()


def test_broken_wheel(tmp_path, artifacts):
    artifacts["wheels"]["bash"].write_text("not a zip")
    with pytest.raises(BundleError, match="invalid wheel"):
        build_bundle(tmp_path / "bad", **artifacts)
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize(
    "pins",
    ["", "pydantic>=2", "pydantic==2.*", "pydantic==2\npydantic==3", "ai-device-bash==0.1.0"],
)
def test_unlocked_or_ambiguous_constraints_rejected(tmp_path, artifacts, pins):
    artifacts["constraints"].write_text(pins)
    with pytest.raises(BundleError):
        build_bundle(tmp_path / "bad", **artifacts)
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("change", ["wheel", "constraints", "missing", "extra", "manifest"])
def test_detect_artifact_or_manifest_changes(tmp_path, artifacts, change):
    output = tmp_path / "release"
    manifest = build_bundle(output, **artifacts)
    if change == "wheel":
        (output / manifest["components"]["fs"]["path"]).write_bytes(b"changed")
    elif change == "constraints":
        (output / "constraints.txt").write_text("pydantic==0\n")
    elif change == "missing":
        (output / manifest["components"]["device"]["path"]).unlink()
    elif change == "extra":
        (output / "unlisted.whl").write_bytes(b"unexpected")
    else:
        manifest["components"]["bash"]["version"] = "9.9.9"
        (output / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BundleError):
        inspect_bundle(output)


@pytest.mark.parametrize("sources", [{}, {"device": "main", "bash": "2" * 40, "fs": "3" * 40}])
def test_full_source_commit_ids_required(tmp_path, artifacts, sources):
    artifacts["sources"] = sources
    with pytest.raises(BundleError, match="sources"):
        build_bundle(tmp_path / "bad", **artifacts)
    assert not (tmp_path / "bad").exists()


def test_publish_failure_removes_only_reserved_empty_directory(tmp_path, artifacts, monkeypatch):
    output = tmp_path / "release"
    real_replace = Path.replace

    def fail_publish(path, target):
        if Path(target) == output:
            raise OSError("simulated publish failure")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_publish)
    with pytest.raises(BundleError, match="publish"):
        build_bundle(output, **artifacts)
    assert not output.exists() and not list(tmp_path.glob(".device-bundle-*"))


def test_cli_build_and_inspect(tmp_path, artifacts):
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps(artifacts["sources"]))
    output = tmp_path / "release"
    command = [
        sys.executable,
        "-m",
        "device_runtime.cli",
        "bundle",
        "build",
        "--output",
        str(output),
        "--version",
        artifacts["release_version"],
        "--constraints",
        str(artifacts["constraints"]),
        "--sources",
        str(sources),
    ]
    for component, wheel in artifacts["wheels"].items():
        command.extend([f"--{component}-wheel", str(wheel)])
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    inspect = subprocess.run(
        [sys.executable, "-m", "device_runtime.cli", "bundle", "inspect", str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert inspect.returncode == 0, inspect.stderr
    assert json.loads(result.stdout) == json.loads(inspect.stdout)
    again = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
    assert again.returncode == 2 and "already exists" in again.stderr


def test_cli_missing_bundle_is_actionable(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "device_runtime.cli", "bundle", "inspect", str(tmp_path / "absent")],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (
        result.returncode == 2
        and "incomplete" in result.stderr
        and "Traceback" not in result.stderr
    )


@pytest.mark.parametrize("version", ["1..0", "1.0..", "1+"])
def test_invalid_pep440_pin_rejected_by_build_and_inspect(tmp_path, artifacts, version):
    output = tmp_path / "release"
    manifest = build_bundle(output, **artifacts)
    artifacts["constraints"].write_text(f"pydantic=={version}\n")
    with pytest.raises(BundleError, match="version"):
        build_bundle(tmp_path / "bad", **artifacts)
    assert not (tmp_path / "bad").exists()
    constraints = output / "constraints.txt"
    constraints.write_text(f"pydantic=={version}\n")
    manifest["constraints"].update(
        sha256=hashlib.sha256(constraints.read_bytes()).hexdigest(), pins={"pydantic": version}
    )
    (output / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BundleError, match="version"):
        inspect_bundle(output)


def test_invalid_pep440_wheel_version_rejected(tmp_path, artifacts):
    artifacts["wheels"]["bash"] = make_wheel(tmp_path, "bash", version="1..0")
    with pytest.raises(BundleError, match="version"):
        build_bundle(tmp_path / "bad", **artifacts)
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize(
    "info_dir",
    [
        "unrelated-9.9.9.dist-info",
        "mcp_server_bash-9.9.9.dist-info",
        "nested/mcp_server_bash-0.2.0.dist-info",
    ],
)
def test_dist_info_identity_and_root_rejected_by_build_and_inspect(tmp_path, artifacts, info_dir):
    output = tmp_path / "release"
    manifest = build_bundle(output, **artifacts)
    wheel = make_wheel(tmp_path, "bash", info_dir=info_dir)
    with pytest.raises(BundleError, match="dist-info"):
        build_bundle(tmp_path / "bad", **artifacts)
    assert not (tmp_path / "bad").exists()
    installed = output / manifest["components"]["bash"]["path"]
    installed.write_bytes(wheel.read_bytes())
    manifest["components"]["bash"]["sha256"] = hashlib.sha256(installed.read_bytes()).hexdigest()
    (output / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BundleError, match="dist-info"):
        inspect_bundle(output)


def _cli_build_args(artifacts, sources, output):
    command = [
        sys.executable,
        "-m",
        "device_runtime.cli",
        "bundle",
        "build",
        "--output",
        str(output),
        "--version",
        artifacts["release_version"],
        "--constraints",
        str(artifacts["constraints"]),
        "--sources",
        str(sources),
    ]
    for component, wheel in artifacts["wheels"].items():
        command.extend([f"--{component}-wheel", str(wheel)])
    return command


def test_output_parent_file_has_controlled_cli_error(tmp_path, artifacts):
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps(artifacts["sources"]))
    parent = tmp_path / "existing-file"
    parent.write_text("keep existing data")
    output = parent / "release"
    result = subprocess.run(
        _cli_build_args(artifacts, sources, output),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2 and "Traceback" not in result.stderr
    assert "cannot read artifacts or publish" in result.stderr
    assert parent.read_text() == "keep existing data" and not output.exists()


def test_output_parent_permission_failure_has_controlled_cli_error(
    tmp_path, artifacts, monkeypatch, capsys
):
    from device_runtime.cli import main

    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps(artifacts["sources"]))
    parent = tmp_path / "denied-parent"
    output = parent / "release"
    real_mkdir = Path.mkdir

    def deny_parent(path, *args, **kwargs):
        if path == parent:
            raise PermissionError("simulated parent permission failure")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_parent)
    monkeypatch.setattr(
        sys, "argv", ["device-runtime", *_cli_build_args(artifacts, sources, output)[3:]]
    )
    with pytest.raises(SystemExit) as exit_result:
        main()
    assert exit_result.value.code == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err and "cannot read artifacts or publish" in captured.err
    assert not output.exists() and not parent.exists()


@pytest.mark.parametrize("field", ["roles", "constraint-pins"])
def test_manifest_role_or_pin_mismatch_rejected(tmp_path, artifacts, field):
    output = tmp_path / "release"
    manifest = build_bundle(output, **artifacts)
    if field == "roles":
        manifest["roles"]["center"]["components"].append("bash")
    else:
        manifest["constraints"]["pins"]["pydantic"] = "2.0.0"
    (output / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BundleError):
        inspect_bundle(output)


def test_format_one_is_not_silently_installed_with_new_roles(tmp_path, artifacts):
    output = tmp_path / "release"
    manifest = build_bundle(output, **artifacts)
    manifest["format_version"] = 1
    (output / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BundleError):
        inspect_bundle(output)
