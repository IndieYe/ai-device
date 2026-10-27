import os

import pytest

from device_runtime import native_bootstrap as bootstrap


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    # Filesystem copy semantics only; real root trust is checked in Linux acceptance.
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 0)
    monkeypatch.setattr(bootstrap.helper, "_trusted", lambda *a, **kw: None)
    source = tmp_path / "package/runtime"
    (source / "bin").mkdir(parents=True)
    python = source / "bin/python3.13"
    python.write_bytes(b"fake interpreter")
    python.chmod(0o755)
    (source / "bin/python").symlink_to("python3.13")
    parent = tmp_path / "manager/transactions"
    parent.mkdir(parents=True)
    return source, parent / "one"


def test_independent_copy_survives_source_changes(runtime):
    source, destination = runtime
    first = bootstrap.snapshot_runtime(source, destination)
    assert bootstrap.snapshot_runtime(source, destination) == first
    original = source / "bin/python3.13"
    copied = destination / "runtime/bin/python3.13"
    assert original.stat().st_ino != copied.stat().st_ino
    assert copied.stat().st_nlink == 1
    original.write_bytes(b"replaced package")
    assert copied.read_bytes() == b"fake interpreter"
    assert bootstrap.validate_snapshot(destination) == first
    assert os.readlink(destination / "runtime/bin/python") == "python3.13"


def test_incomplete_copy_never_replaced(runtime):
    source, destination = runtime
    destination.mkdir()
    with pytest.raises(FileNotFoundError):
        bootstrap.snapshot_runtime(source, destination)
    assert list(destination.iterdir()) == []


def test_snapshot_drift_refused(runtime):
    source, destination = runtime
    bootstrap.snapshot_runtime(source, destination)
    (destination / "runtime/bin/python3.13").write_bytes(b"changed")
    with pytest.raises(bootstrap.BootstrapError, match="drift"):
        bootstrap.validate_snapshot(destination)


@pytest.mark.parametrize("target", ["/bin/sh", "../../outside"])
def test_external_links_refused_before_copy(runtime, target):
    source, destination = runtime
    (source / "bin/external").symlink_to(target)
    with pytest.raises(bootstrap.BootstrapError, match="inside"):
        bootstrap.snapshot_runtime(source, destination)
    assert not destination.exists()


def test_copy_source_drift_leaves_blocked_directory(runtime, monkeypatch):
    source, destination = runtime
    real_copy = bootstrap.shutil.copytree

    def changing(*a, **kw):
        result = real_copy(*a, **kw)
        (source / "bin/python3.13").write_bytes(b"changed during copy")
        return result

    monkeypatch.setattr(bootstrap.shutil, "copytree", changing)
    with pytest.raises(bootstrap.BootstrapError, match="changed while"):
        bootstrap.snapshot_runtime(source, destination)
    assert destination.exists() and not (destination / "snapshot.json").exists()


def test_nonadministrator_refused(runtime, monkeypatch):
    source, destination = runtime
    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 12345)
    with pytest.raises(bootstrap.BootstrapError, match="administrator"):
        bootstrap.snapshot_runtime(source, destination)


def test_debian_sender_uses_only_bound_snapshot(runtime, monkeypatch):
    from device_runtime import native_update as update

    source, destination = runtime
    stem = destination.parent / "operation"
    monkeypatch.setattr(update.sys, "base_prefix", str(source))
    adapter = update.Debian()
    intent = adapter.prepare_execution(stem)
    calls = []
    monkeypatch.setattr(update.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    receipt = stem.with_suffix(".sender.json")
    adapter.apply(source / "package.deb", lease_fd=3, receipt_path=receipt, intent=intent)
    argv, kwargs = calls[0]
    assert argv[0] == str(stem.with_suffix(".bootstrap") / "runtime/bin/python3.13")
    assert argv[1:3] == ["-I", "-B"]
    assert kwargs["pass_fds"] == (3,)
    assert kwargs["env"]["PATH"] == "/usr/sbin:/usr/bin:/sbin:/bin"
    assert kwargs.get("timeout") is None
    with pytest.raises(update.UpdateError, match="snapshot changed"):
        adapter.apply(source / "package.deb", lease_fd=3, receipt_path=receipt, intent={})
    assert len(calls) == 1


@pytest.mark.parametrize("code,query_ok", [(0, True), (1, True), (0, False)])
def test_guard_owns_durable_outcome(runtime, monkeypatch, code, query_ok):
    import json
    import subprocess
    from types import SimpleNamespace

    from device_runtime import native_update as update

    source, destination = runtime
    stem = destination.parent / "guard"
    monkeypatch.setattr(update.sys, "base_prefix", str(source))
    adapter = update.Debian()
    intent = adapter.prepare_execution(stem)
    intent["target_package"] = {"name": "fixture", "version": "2", "architecture": "amd64"}
    captured = []
    monkeypatch.setattr(update.subprocess, "run", lambda argv, **kw: captured.append(argv))
    receipt = stem.with_suffix(".sender.json")
    adapter.apply(source / "fixture.deb", lease_fd=3, receipt_path=receipt, intent=intent)
    argv = captured[0]

    def command(command, **kw):
        if command[0] == "/usr/bin/dpkg":
            assert kw["stdout"].name.endswith(".package.log")
            return SimpleNamespace(returncode=code)
        if not query_ok:
            raise subprocess.TimeoutExpired(command, 30)
        return SimpleNamespace(returncode=0, stdout="install ok installed\n2\namd64\n")

    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(update.sys, "argv", ["guard", *argv[5:]])
    with pytest.raises(SystemExit) as exited:
        # Execute only the captured repository-owned guard, never external input.
        exec(compile(argv[4], "independent-guard", "exec"), {})  # noqa: S102
    assert exited.value.code == code
    completion = json.loads(receipt.read_bytes())
    assert completion["intent"] == intent and completion["returncode"] == code
    outcome = json.loads(stem.with_suffix(".outcome.json").read_bytes())
    expected = "package_applied_pending_activation" if code == 0 and query_ok else "unknown"
    assert outcome["state"] == expected and outcome["intent"] == intent


def test_worker_already_inside_snapshot_does_not_recopy(runtime, monkeypatch):
    from device_runtime import native_update as update

    source, destination = runtime
    stem = destination.parent / "inside"
    monkeypatch.setattr(update.sys, "base_prefix", str(source))
    adapter = update.Debian()
    first = adapter.prepare_execution(stem)
    snapshot = stem.with_suffix(".bootstrap")
    monkeypatch.setattr(update.sys, "base_prefix", str(snapshot / "runtime"))
    assert adapter.prepare_execution(stem) == first
