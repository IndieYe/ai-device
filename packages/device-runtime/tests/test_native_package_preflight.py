"""Fail-closed native package preflight across versioned layout roots."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "existing",
    [None, "packages/old-version", "bootstrap/old-version", "instances", "manager"],
)
def test_deb_preflight_rejects_existing_versioned_layout(tmp_path, existing):
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    script = source.split("cat > \"$work/preflight\" <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    root = tmp_path / "opt/ai-device"
    manager = tmp_path / "var/lib/ai-device/manager"
    script = script.replace("/opt/ai-device", str(root)).replace(
        "/var/lib/ai-device/manager", str(manager)
    )
    if existing:
        path = manager if existing == "manager" else root / existing
        path.mkdir(parents=True)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, check=False)
    assert (result.returncode == 0) == (existing is None)
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before


def test_rpm_preflight_guards_layout_roots():
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text().split("%pretrans -p <lua>", 1)[1].split("%preun", 1)[0]
    assert "'/opt/ai-device/packages'" in source
    assert "'/opt/ai-device/bootstrap'" in source
    assert "helper transaction required" in source


@pytest.mark.parametrize("name", ["packages", "bootstrap"])
def test_deb_preflight_rejects_dangling_layout_symlink(tmp_path, name):
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    script = source.split("cat > \"$work/preflight\" <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    root = tmp_path / "opt/ai-device"
    root.mkdir(parents=True)
    (root / name).symlink_to(root / "missing")
    script = script.replace("/opt/ai-device", str(root)).replace(
        "/var/lib/ai-device/manager", str(tmp_path / "manager")
    )
    result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, check=False)
    assert result.returncode != 0
