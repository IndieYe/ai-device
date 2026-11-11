import copy
import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/client-macos-resource-manifest.py"
spec = importlib.util.spec_from_file_location("mac_resource_manifest", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    root.mkdir(mode=0o755)
    (root / "directory").mkdir(mode=0o755)
    (root / "directory/file").write_bytes(b"version B")
    (root / "directory/file").chmod(0o644)
    (root / "alias").symlink_to("directory/file")
    return root


def test_deterministic_exact_inventory(tree):
    manifest = module.inventory(tree)
    assert manifest == module.inventory(tree)
    module.verify(tree, manifest)
    assert manifest["total_file_bytes"] == 9
    assert len(manifest["entries"]) == 3


@pytest.mark.parametrize("change", ["content", "mode", "extra", "missing", "link"])
def test_mutation_rejected(tree, change):
    manifest = module.inventory(tree)
    if change == "content":
        (tree / "directory/file").write_bytes(b"version C")
    elif change == "mode":
        (tree / "directory/file").chmod(0o755)
    elif change == "extra":
        (tree / "extra").write_text("extra")
    elif change == "missing":
        (tree / "alias").unlink()
    else:
        (tree / "alias").unlink()
        (tree / "alias").symlink_to("directory")
    with pytest.raises(ValueError):
        module.verify(tree, manifest)


@pytest.mark.parametrize(
    "kind", ["fifo", "hardlink", "public", "setuid", "broken", "escape", "loop"]
)
def test_unsupported_resource_rejected(tree, kind, tmp_path):
    if kind == "fifo":
        os.mkfifo(tree / "fifo")
    elif kind == "hardlink":
        os.link(tree / "directory/file", tree / "hardlink")
    elif kind == "public":
        (tree / "directory").chmod(0o777)
    elif kind == "setuid":
        (tree / "directory/file").chmod(0o4755)
    elif kind == "broken":
        (tree / "bad").symlink_to("missing")
    elif kind == "loop":
        (tree / "bad").symlink_to("bad")
    else:
        (tmp_path / "outside").write_text("not resource")
        (tree / "bad").symlink_to("../outside")
    with pytest.raises(ValueError):
        module.inventory(tree)


@pytest.mark.parametrize("change", ["unknown", "duplicate", "version", "total"])
def test_manifest_shape_cannot_silently_drop_records(tree, change):
    manifest = copy.deepcopy(module.inventory(tree))
    if change == "unknown":
        manifest["extra"] = True
    elif change == "duplicate":
        manifest["entries"].append(manifest["entries"][0])
    elif change == "version":
        manifest["schema_version"] = 2
    else:
        manifest["total_file_bytes"] += 1
    with pytest.raises(ValueError):
        module.verify(tree, manifest)


def test_size_budget_is_enforced(tree, monkeypatch):
    monkeypatch.setattr(module, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="byte limit"):
        module.inventory(tree)


def test_entry_budget_is_enforced(tree, monkeypatch):
    monkeypatch.setattr(module, "MAX_ENTRIES", 1)
    with pytest.raises(ValueError, match="entry limit"):
        module.inventory(tree)


@pytest.mark.parametrize("state", ["target_exact", "prior_exact", "absent", "unknown"])
def test_publication_observation_never_claims_ready_or_repairs(tree, tmp_path, state):
    import shutil

    prior = module.inventory(tree)
    (tree / "directory/file").write_bytes(b"version C")
    target = module.inventory(tree)
    root = tmp_path / "public"
    if state != "absent":
        shutil.copytree(tree, root, symlinks=True)
        if state == "prior_exact":
            (root / "directory/file").write_bytes(b"version B")
        elif state == "unknown":
            (root / "extra").write_text("unrecognized")
    result = module.observe_publication(root, target, prior_manifest=prior)
    assert result == {"tree": state, "ready": "not_checked"}
    if state == "unknown":
        assert (root / "extra").read_text() == "unrecognized"


def test_publication_permission_error_is_unknown(tree, monkeypatch):
    target = module.inventory(tree)

    def unavailable(root):
        raise PermissionError("not readable")

    monkeypatch.setattr(module, "inventory", unavailable)
    assert module.observe_publication(tree, target) == {"tree": "unknown", "ready": "not_checked"}


def test_broken_public_root_is_unknown_not_absent(tree, tmp_path):
    target = module.inventory(tree)
    root = tmp_path / "public"
    root.symlink_to(tmp_path / "missing")
    assert module.observe_publication(root, target)["tree"] == "unknown"
