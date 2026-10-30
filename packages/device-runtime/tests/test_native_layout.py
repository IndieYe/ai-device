"""The pre-install classifier both package managers consult.

The previous hooks refused any existing layout, which was correct while no
upgrade path existed but also meant a registered instance could never move to a
new package. The rule these tests pin is narrower: refuse what cannot be
classified. The bootstrap pointer decides what is installed, and it may name one
tree while retired trees from earlier upgrades stay beside it.
"""

from pathlib import Path

import pytest

from device_runtime import native_layout as layout


@pytest.fixture
def roots(tmp_path, monkeypatch):
    packages = tmp_path / "packages"
    bootstrap = tmp_path / "bootstrap"
    monkeypatch.setattr(layout, "PACKAGES", packages)
    monkeypatch.setattr(layout, "BOOTSTRAP", bootstrap)
    return packages, bootstrap


def _payload(packages, name="0.2.2-native1"):
    directory = packages / name
    (directory / "runtime" / "bin").mkdir(parents=True)
    (directory / "runtime" / "bin" / "python3.13").write_text("#!/bin/sh\n")
    (directory / "bundle").mkdir()
    (directory / "bundle" / "manifest.json").write_text("{}")
    return directory


def _bootstrap(bootstrap, name="0.2.2-native1"):
    directory = bootstrap / name
    (directory / "runtime" / "bin").mkdir(parents=True)
    (directory / "runtime" / "bin" / "python3.13").write_text("#!/bin/sh\n")
    return directory


def _pointer(bootstrap, name="0.2.2-native1"):
    """Publish the live pointer the way the post-install step does."""
    bootstrap.mkdir(parents=True, exist_ok=True)
    (bootstrap / "current").write_text(name + "\n")


def test_empty_layout_is_a_fresh_install(roots):
    assert layout.classify()["kind"] == "empty"


def test_one_complete_versioned_payload_is_an_upgrade(roots):
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    _pointer(bootstrap)

    observation = layout.classify()

    assert observation["kind"] == "upgrade"
    assert observation["packages"] == ["0.2.2-native1"]
    assert observation["live"] == "0.2.2-native1"


def test_incomplete_payload_is_refused(roots):
    """Half-written state must not look upgradeable."""
    packages, bootstrap = roots
    (packages / "0.2.2-native1").mkdir(parents=True)
    _bootstrap(bootstrap)
    _pointer(bootstrap)

    with pytest.raises(layout.LayoutError, match="incomplete"):
        layout.classify()


def test_incomplete_bootstrap_is_refused(roots):
    packages, bootstrap = roots
    _payload(packages)
    (bootstrap / "0.2.2-native1").mkdir(parents=True)
    _pointer(bootstrap)

    with pytest.raises(layout.LayoutError, match="incomplete"):
        layout.classify()


def test_a_retired_tree_beside_the_live_one_is_allowed(roots):
    """An upgrade publishes a new tree and leaves the old one on disk.

    The pointer says which is live, so a second tree is accounted for. Refusing
    it would make every completed upgrade refuse the next one.
    """
    packages, bootstrap = roots
    _payload(packages, "0.2.2-native1")
    _payload(packages, "0.3.0-native2")
    _bootstrap(bootstrap, "0.2.2-native1")
    _bootstrap(bootstrap, "0.3.0-native2")
    _pointer(bootstrap, "0.3.0-native2")

    observation = layout.classify()

    assert observation["kind"] == "upgrade"
    assert observation["live"] == "0.3.0-native2"
    assert observation["packages"] == ["0.2.2-native1", "0.3.0-native2"]


def test_trees_without_a_pointer_are_refused(roots):
    """An interrupted publish leaves trees but no live pointer."""
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)

    with pytest.raises(layout.LayoutError, match="pointer"):
        layout.classify()


def test_pointer_naming_an_absent_tree_is_refused(roots):
    """A pointer must name a tree that is actually installed on both sides."""
    packages, bootstrap = roots
    _payload(packages, "0.2.2-native1")
    _bootstrap(bootstrap, "0.2.2-native1")
    _pointer(bootstrap, "0.9.9-absent")

    with pytest.raises(layout.LayoutError, match="absent"):
        layout.classify()


def test_symlinked_root_is_refused(roots, tmp_path):
    """A symlink here could redirect the package into an unrelated tree."""
    packages, bootstrap = roots
    target = tmp_path / "elsewhere"
    target.mkdir()
    packages.symlink_to(target)
    _bootstrap(bootstrap)

    with pytest.raises(layout.LayoutError, match="symlink"):
        layout.classify()


def test_symlinked_version_directory_is_refused(roots, tmp_path):
    packages, bootstrap = roots
    real = tmp_path / "real"
    real.mkdir()
    (packages).mkdir()
    (packages / "0.2.2-native1").symlink_to(real)
    _bootstrap(bootstrap)

    with pytest.raises(layout.LayoutError, match="plain directory"):
        layout.classify()


def test_stray_file_is_refused(roots):
    """A non-directory child is unclassifiable, not a second version."""
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    (packages / "stray").write_text("not a directory")

    with pytest.raises(layout.LayoutError, match="plain directory"):
        layout.classify()


def test_reinstalling_the_same_version_is_refused(roots, capsys):
    """In-place replacement while instances run from the tree is not an upgrade."""
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    _pointer(bootstrap)

    assert layout.main(["native_layout", "check", "0.2.2-native1"]) == 1
    assert "already installed" in capsys.readouterr().err


def test_adding_a_new_version_is_allowed(roots, capsys):
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    _pointer(bootstrap)

    assert layout.main(["native_layout", "check", "0.3.0-native2"]) == 0
    assert "adding 0.3.0-native2" in capsys.readouterr().err


def test_fresh_install_needs_no_target_name(roots):
    assert layout.main(["native_layout", "check"]) == 0


def test_classification_error_becomes_a_refusal_exit(roots, capsys):
    packages, bootstrap = roots
    _payload(packages, "0.2.2-native1")
    _payload(packages, "0.3.0-native2")
    _bootstrap(bootstrap)

    assert layout.main(["native_layout", "check", "0.4.0-native3"]) == 1
    assert "refused" in capsys.readouterr().err


@pytest.mark.parametrize("target", [("instances", "manager")])
def test_legacy_management_layouts_are_refused(tmp_path, roots, monkeypatch, target):
    """Legacy administrator state blocks a write; a normal instance does not.

    /opt/ai-device/instances is where ai-device-native-install puts an instance,
    so refusing on its mere presence made every upgrade on a host with a
    registered instance fail -- the opposite of the intent.
    """
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    name, child = target
    root = tmp_path / name
    monkeypatch.setattr(layout, "INSTANCE_ROOT", root)
    monkeypatch.setattr(layout, "INSTANCE_MARKERS", (tmp_path / "manager",))
    marker = root / child if child else tmp_path / "manager"
    marker.mkdir(parents=True)

    with pytest.raises(layout.LayoutError, match="legacy management layout"):
        layout.classify()


def test_ordinary_instance_does_not_block_an_upgrade(tmp_path, roots, monkeypatch):
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    _pointer(bootstrap)
    root = tmp_path / "instances"
    monkeypatch.setattr(layout, "INSTANCE_ROOT", root)
    monkeypatch.setattr(layout, "INSTANCE_MARKERS", (tmp_path / "manager",))
    (root / "probe").mkdir(parents=True)

    assert layout.classify()["kind"] == "upgrade"


@pytest.mark.parametrize("target", [("instances", "manager")])
def test_dangling_management_symlink_is_refused(tmp_path, roots, monkeypatch, target):
    """A dangling symlink still names state we must not write over."""
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    name, child = target
    root = tmp_path / name
    monkeypatch.setattr(layout, "INSTANCE_ROOT", root)
    monkeypatch.setattr(layout, "INSTANCE_MARKERS", (tmp_path / "manager",))
    marker = root / child if child else tmp_path / "manager"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.symlink_to(tmp_path / "missing")

    with pytest.raises(layout.LayoutError, match="legacy management layout"):
        layout.classify()

def test_pointer_beside_versioned_trees_is_accepted(roots):
    """The pointer lives next to the trees it selects, so it is part of the layout.

    Treating it as a stray child made every upgrade after a pointer existed
    refuse with "must be a plain directory" -- the classifier rejected the very
    file the upgrade writes.
    """
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    (bootstrap / "current").write_text("0.2.2-native1\n")

    observation = layout.classify()

    assert observation["kind"] == "upgrade"


def test_pointer_must_be_a_plain_file(roots):
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    pointer = bootstrap / "current"
    pointer.mkdir()

    with pytest.raises(layout.LayoutError, match="must be a regular file"):
        layout.classify()


@pytest.mark.parametrize("value", ["", "../escape", "/abs", "a/b", "-flag", ".hidden"])
def test_pointer_must_name_a_directory(roots, value):
    """A pointer that is not a directory name cannot be joined to a root."""
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    (bootstrap / "current").write_text(value + "\n")

    with pytest.raises(layout.LayoutError):
        layout.classify()


def test_legacy_manager_root_is_refused(roots, monkeypatch, tmp_path):
    """The absolute manager directory is legacy state, not a normal path.

    rpm's pretrans guard refuses it. The dpkg classifier used to skip it
    because INSTANCE_MARKERS was declared but never walked, so the two
    package managers disagreed about the same machine.
    """
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    manager = tmp_path / "manager"
    manager.mkdir()
    monkeypatch.setattr(layout, "INSTANCE_MARKERS", (manager,))
    monkeypatch.setattr(layout, "INSTANCE_ROOT", tmp_path / "instances")

    with pytest.raises(layout.LayoutError, match="legacy management layout"):
        layout.classify()


def test_legacy_manager_root_dangling_symlink_is_refused(roots, monkeypatch, tmp_path):
    packages, bootstrap = roots
    _payload(packages)
    _bootstrap(bootstrap)
    manager = tmp_path / "manager"
    manager.symlink_to(tmp_path / "missing")
    monkeypatch.setattr(layout, "INSTANCE_MARKERS", (manager,))
    monkeypatch.setattr(layout, "INSTANCE_ROOT", tmp_path / "instances")

    with pytest.raises(layout.LayoutError, match="legacy management layout"):
        layout.classify()


def test_guard_targets_match_the_rpm_pretrans_list():
    """Both package managers must refuse the same machines.

    The rpm pretrans guard names these two paths literally. If the dpkg
    classifier drifts from that list, an upgrade succeeds on Debian and
    fails on RHEL for the same host state.
    """
    assert layout.INSTANCE_ROOT == Path("/opt/ai-device/instances")
    assert layout.INSTANCE_NAMES == ("manager",)
    assert layout.INSTANCE_MARKERS == (Path("/var/lib/ai-device/manager"),)
