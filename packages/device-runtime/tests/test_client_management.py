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


def test_rpm_host_does_not_require_the_dpkg_locks(tmp_path, monkeypatch):
    """Measured on alinux3: the dpkg-only version opened
    /var/lib/dpkg/lock-frontend unconditionally and raised FileNotFoundError
    before doing anything, so neither activate nor register could run at all on
    an RPM host."""
    monkeypatch.setattr(module, "DPKG_LOCKS", (tmp_path / "frontend", tmp_path / "dpkg"))
    monkeypatch.setattr(module, "MANAGEMENT_LOCK", tmp_path / "management")
    monkeypatch.setattr(module, "package_manager", lambda: "rpm")

    with module.management_lock():
        # The management lock is still held; only the dpkg locks are skipped.
        assert child_probe(module.MANAGEMENT_LOCK, "flock") != 0
    assert child_probe(module.MANAGEMENT_LOCK, "flock") == 0


def test_a_missing_dpkg_lock_file_is_skipped_not_fatal(tmp_path, monkeypatch):
    """dpkg creates its lock files on first use. A host that has dpkg installed
    but has never run it must not break manual management."""
    existing = tmp_path / "frontend"
    existing.touch()
    monkeypatch.setattr(module, "DPKG_LOCKS", (existing, tmp_path / "absent"))
    monkeypatch.setattr(module, "MANAGEMENT_LOCK", tmp_path / "management")
    monkeypatch.setattr(module, "package_manager", lambda: "dpkg")

    with module.management_lock():
        assert child_probe(existing) != 0


def test_package_manager_detection_prefers_dpkg(tmp_path, monkeypatch):
    lock = tmp_path / "frontend"
    monkeypatch.setattr(module, "DPKG_LOCKS", (lock, tmp_path / "dpkg"))
    assert module.package_manager() is None
    lock.touch()
    assert module.package_manager() == "dpkg"


def test_rpm_scriptlet_is_recognised_by_parent_process(monkeypatch):
    """Measured on alinux3: an rpm scriptlet sees no RPM_* environment variable
    at all. An env-based check reported "manual" and then tried to take dpkg
    locks that do not exist there. The parent process is rpm, which is the
    signal that actually works."""
    monkeypatch.delenv("DPKG_MAINTSCRIPT_PACKAGE", raising=False)
    monkeypatch.delenv("DPKG_MAINTSCRIPT_NAME", raising=False)
    monkeypatch.setattr(module.os, "getppid", lambda: 4242)
    monkeypatch.setattr(module.Path, "read_text", lambda self: "rpm\n")
    assert module.in_package_script()


def test_manual_run_is_not_mistaken_for_a_scriptlet(monkeypatch):
    monkeypatch.delenv("DPKG_MAINTSCRIPT_PACKAGE", raising=False)
    monkeypatch.setattr(module.os, "getppid", lambda: 4242)
    monkeypatch.setattr(module.Path, "read_text", lambda self: "bash\n")
    assert not module.in_package_script()
