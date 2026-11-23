import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/client_package_version.py"
spec = importlib.util.spec_from_file_location("client_package_version", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _tree(tmp_path):
    tree = tmp_path / "tree"
    (tree / "runtime/bin").mkdir(parents=True)
    (tree / "runtime/bin/python3").write_text("fixture")
    return tree


def test_writes_version_at_tree_root(tmp_path):
    tree = _tree(tmp_path)
    path = module.write_package_version(tree, "0.5.5")
    assert path == tree / "client-package-version.json"
    assert json.loads(path.read_text()) == {
        "schema_version": 1,
        "package_version": "0.5.5",
    }


def test_written_file_is_read_by_the_client(tmp_path, monkeypatch):
    """The writer and device_runtime.system_info must agree on the contract."""
    from device_runtime import system_info

    root = tmp_path / "root"
    package = root / "runtime/lib/python3.13/site-packages/device_runtime"
    package.mkdir(parents=True)
    module.write_package_version(root, "0.5.5")
    monkeypatch.setattr(system_info, "__file__", str(package / "system_info.py"))
    assert system_info.collect_package_version() == "0.5.5"


@pytest.mark.parametrize("version", ["", "has space", "a/b", "x" * 81, "../1.0"])
def test_refuses_versions_the_client_cannot_parse(tmp_path, version):
    with pytest.raises(ValueError, match="invalid package version"):
        module.write_package_version(_tree(tmp_path), version)


def test_refuses_to_overwrite(tmp_path):
    tree = _tree(tmp_path)
    module.write_package_version(tree, "0.5.5")
    with pytest.raises(ValueError, match="already present"):
        module.write_package_version(tree, "0.5.6")


def test_refuses_a_directory_that_is_not_a_client_tree(tmp_path):
    with pytest.raises(ValueError, match="not a client tree"):
        module.write_package_version(tmp_path / "empty", "0.5.5")


def test_directories_are_usable_under_a_077_umask(tmp_path):
    """A root builder with umask 077 must not ship 0700 directories.

    dpkg-deb rejects that only for DEBIAN; for the payload it packages the mode
    verbatim, so the package installs and the service user cannot read it.
    """
    previous = os.umask(0o077)
    try:
        root = tmp_path / "root"
        payload = root / "opt/ai-device-client"
        control = root / "DEBIAN"
        for directory in (payload, control):
            directory.mkdir(parents=True, exist_ok=True)
        assert stat.S_IMODE(payload.stat().st_mode) == 0o700
        module.ensure_readable_directories(root, payload, control)
        for directory in (root, payload, control):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    finally:
        os.umask(previous)


def test_normalising_refuses_a_missing_directory(tmp_path):
    """A typo'd path must fail instead of silently leaving a 0700 directory."""
    with pytest.raises(ValueError, match="not a directory to normalise"):
        module.ensure_readable_directories(tmp_path / "absent")


def test_version_file_is_readable_by_a_non_privileged_service_user(tmp_path):
    """A root build under umask 077 must not ship a 0600 version file.

    write_text() creates with 0666 & ~umask, so the file measured 0600 inside a
    real package. The runtime reads it as the service user; measured on Linux,
    a non-privileged user got Permission denied and the version came back None.
    """
    tree = _tree(tmp_path)
    previous = os.umask(0o077)
    try:
        path = module.write_package_version(tree, "0.5.6")
    finally:
        os.umask(previous)
    assert path.stat().st_mode & 0o777 == 0o644
