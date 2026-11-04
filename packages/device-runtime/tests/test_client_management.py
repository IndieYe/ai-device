import subprocess
import sys

import pytest

from device_runtime import client_management as module


@pytest.fixture
def locks(tmp_path, monkeypatch):
    paths = (tmp_path / "frontend", tmp_path / "dpkg")
    for path in paths:
        path.touch()
    monkeypatch.setattr(module, "DPKG_LOCKS", paths)
    monkeypatch.setattr(module, "MANAGEMENT_LOCK", tmp_path / "management")
    return paths


def child_probe(path, kind="lockf"):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,sys; f=open(sys.argv[1],'r+'); "
                f"fcntl.{kind}(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
            ),
            str(path),
        ],
        capture_output=True,
        check=False,
    ).returncode


def test_manual_holds_both_package_locks_and_releases_on_error(locks):
    with pytest.raises(ValueError), module.management_lock():
        assert all(child_probe(path) != 0 for path in locks)
        assert child_probe(module.MANAGEMENT_LOCK, "flock") != 0
        raise ValueError("test")
    assert all(child_probe(path) == 0 for path in locks)
    assert child_probe(module.MANAGEMENT_LOCK, "flock") == 0


def test_package_script_uses_management_lock_without_relocking_dpkg(locks):
    with module.management_lock(package_script=True):
        assert all(child_probe(path) == 0 for path in locks)
        assert child_probe(module.MANAGEMENT_LOCK, "flock") != 0


@pytest.mark.parametrize("name,expected", [("postinst", True), ("prerm", True), ("other", False)])
def test_package_context(monkeypatch, name, expected):
    monkeypatch.setenv("DPKG_MAINTSCRIPT_PACKAGE", "ai-device-client")
    monkeypatch.setenv("DPKG_MAINTSCRIPT_NAME", name)
    assert module.in_package_script() is expected
    monkeypatch.setenv("DPKG_MAINTSCRIPT_PACKAGE", "unrelated")
    assert not module.in_package_script()
