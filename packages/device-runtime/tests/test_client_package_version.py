import importlib.util
import json
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
