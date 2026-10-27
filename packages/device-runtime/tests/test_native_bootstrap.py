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


def test_helper_candidate_probe_is_bound_and_nonactivating(runtime, monkeypatch):
    import json
    import subprocess
    from types import SimpleNamespace

    source, destination = runtime
    module = source / "lib/python3.13/site-packages/device_runtime/native_helper.py"
    module.parent.mkdir(parents=True)
    module.write_text("# fixture")
    bootstrap.snapshot_runtime(source, destination)
    candidate = destination / "runtime"
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "base": str(candidate),
                    "module": str(
                        candidate / "lib/python3.13/site-packages/device_runtime/native_helper.py"
                    ),
                    "version": [3, 13],
                }
            )
        )

    monkeypatch.setattr(subprocess, "run", run)
    before = sorted(str(p.relative_to(destination)) for p in destination.rglob("*"))
    result = bootstrap.inspect_helper_candidate(destination)
    assert (
        result["activated"] is False and result["state"] == "helper_candidate_checked_not_activated"
    )
    assert result["snapshot_sha256"] == bootstrap._digest(destination / "snapshot.json")
    assert calls[0][0][1:3] == ["-I", "-B"] and calls[0][1]["timeout"] == 30
    assert sorted(str(p.relative_to(destination)) for p in destination.rglob("*")) == before


@pytest.mark.parametrize("failure", ["identity", "timeout", "drift"])
def test_helper_candidate_probe_refuses_failure(runtime, monkeypatch, failure):
    import json
    import subprocess
    from types import SimpleNamespace

    source, destination = runtime
    module = source / "lib/python3.13/site-packages/device_runtime/native_helper.py"
    module.parent.mkdir(parents=True)
    module.write_text("# fixture")
    bootstrap.snapshot_runtime(source, destination)
    candidate = destination / "runtime"

    def run(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 30)
        if failure == "drift":
            (candidate / "bin/python3.13").write_bytes(b"changed")
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "base": "/wrong" if failure == "identity" else str(candidate),
                    "module": str(
                        candidate / "lib/python3.13/site-packages/device_runtime/native_helper.py"
                    ),
                    "version": [3, 13],
                }
            )
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.inspect_helper_candidate(destination)


@pytest.mark.parametrize("cache", ["__pycache__/native_helper.cpython-313.pyc", "native_helper.pyc"])
def test_helper_candidate_refuses_snapshotted_bytecode(runtime, monkeypatch, cache):
    import subprocess

    source, destination = runtime
    package = source / "lib/python3.13/site-packages/device_runtime"
    package.mkdir(parents=True)
    (package / "native_helper.py").write_text("# source")
    bytecode = package / cache
    bytecode.parent.mkdir(parents=True, exist_ok=True)
    bytecode.write_bytes(b"old bytecode")
    bootstrap.snapshot_runtime(source, destination)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(bootstrap.BootstrapError, match="bytecode cache"):
        bootstrap.inspect_helper_candidate(destination)
    assert not calls
    assert bytecode.read_bytes() == b"old bytecode"


def test_read_helper_candidate_refuses_bytecode_in_verified_snapshot(runtime):
    source, destination = runtime
    package = source / "lib/python3.13/site-packages/device_runtime"
    package.mkdir(parents=True)
    (package / "native_helper.py").write_text("# source")
    (package / "native_helper.pyc").write_bytes(b"old bytecode")
    candidate = destination.parent / "v1"
    bootstrap.snapshot_runtime(source, candidate)
    with pytest.raises(bootstrap.BootstrapError, match="bytecode cache"):
        bootstrap.read_helper_candidate(destination.parent, "v1")
    assert (candidate / "runtime/lib/python3.13/site-packages/device_runtime/native_helper.pyc").exists()


def test_publish_versioned_candidate_never_activates(runtime, monkeypatch):
    source, destination = runtime
    module = source / "lib/python3.13/site-packages/device_runtime/native_helper.py"
    module.parent.mkdir(parents=True)
    module.write_text("# fixture")

    def inspect(candidate):
        bootstrap.validate_snapshot(candidate)
        return {
            "snapshot_sha256": bootstrap._digest(candidate / "snapshot.json"),
            "helper_sha256": bootstrap._digest(
                candidate / "runtime/lib/python3.13/site-packages/device_runtime/native_helper.py"
            ),
        }

    monkeypatch.setattr(bootstrap, "inspect_helper_candidate", inspect)
    first = bootstrap.publish_helper_candidate(source, destination.parent, "v1")
    assert first["version"] == "v1" and first["activated"] is False
    assert bootstrap.publish_helper_candidate(source, destination.parent, "v1") == first
    receipt = destination.parent / "v1/candidate.json"
    receipt.write_text("{}")
    with pytest.raises(bootstrap.BootstrapError, match="publication evidence differs"):
        bootstrap.publish_helper_candidate(source, destination.parent, "v1")
    assert receipt.read_text() == "{}"


def test_publish_refuses_invalid_or_incomplete_version(runtime, monkeypatch):
    source, destination = runtime
    with pytest.raises(ValueError, match="identifier"):
        bootstrap.publish_helper_candidate(source, destination.parent, "../bad")
    candidate = destination.parent / "v2"
    candidate.mkdir()
    with pytest.raises(FileNotFoundError):
        bootstrap.publish_helper_candidate(source, destination.parent, "v2")
    assert list(candidate.iterdir()) == []


def test_managed_candidate_publishes_under_lock_and_read_rejects_drift(runtime, monkeypatch):
    from contextlib import contextmanager

    source, destination = runtime
    module = source / "lib/python3.13/site-packages/device_runtime/native_helper.py"
    module.parent.mkdir(parents=True)
    module.write_text("# fixture")
    registry = destination.parent
    candidates = registry / "bootstrap-candidates"
    candidates.mkdir()
    held = []

    @contextmanager
    def lease(given, *, exclusive=False):
        assert given == registry and exclusive
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    def inspect(candidate):
        assert held
        bootstrap.validate_snapshot(candidate)
        return {
            "snapshot_sha256": bootstrap._digest(candidate / "snapshot.json"),
            "helper_sha256": bootstrap._digest(
                candidate / "runtime/lib/python3.13/site-packages/device_runtime/native_helper.py"
            ),
        }

    monkeypatch.setattr(bootstrap.helper, "package_lease", lease)
    monkeypatch.setattr(bootstrap, "inspect_helper_candidate", inspect)
    result = bootstrap.publish_managed_helper_candidate(registry, source, "v1")
    assert not held and bootstrap.read_helper_candidate(candidates, "v1") == result
    receipt = candidates / "v1/candidate.json"
    receipt.write_text("{}")
    with pytest.raises(bootstrap.BootstrapError, match="publication evidence differs"):
        bootstrap.read_helper_candidate(candidates, "v1")
    (candidates / "v1/runtime/bin/python3.13").write_bytes(b"drift")
    with pytest.raises(bootstrap.BootstrapError, match="drift"):
        bootstrap.read_helper_candidate(candidates, "v1")


def test_managed_candidate_refuses_busy_lock(runtime):
    source, destination = runtime
    registry = destination.parent
    (registry / "bootstrap-candidates").mkdir()
    with (
        bootstrap.helper.package_lease(registry, exclusive=True),
        pytest.raises(ValueError, match="still executing"),
    ):
        bootstrap.publish_managed_helper_candidate(registry, source, "v1")
    assert not (registry / "bootstrap-candidates/v1").exists()


def test_verified_helper_candidate_executes_checked_bytes(runtime, monkeypatch):
    import hashlib
    import sys
    from types import SimpleNamespace

    source, destination = runtime
    site = source / "lib/python3.13/site-packages/device_runtime"
    site.mkdir(parents=True)
    (site / "__init__.py").write_text("value = 'candidate'\n")
    (site / "management_lock.py").write_text("value = 'checked'\n")
    (site / "native_helper.py").write_text(
        "from .management_lock import value\ndef serve(registry, socket_path, *, group):\n"
        "    assert (value, registry, socket_path, group) == ('checked', 'r', 's', 7)\n"
    )
    candidate = destination.parent / "v1"
    snapshot = bootstrap.snapshot_runtime(source, candidate)
    evidence = {
        "snapshot_sha256": bootstrap._digest(candidate / "snapshot.json"),
        "helper_sha256": hashlib.sha256((site / "native_helper.py").read_bytes()).hexdigest(),
    }
    monkeypatch.setattr(bootstrap, "read_helper_candidate", lambda *_: evidence)
    real_sys = bootstrap.sys
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(
        platform="linux", executable=str(candidate / "runtime/bin/python3.13"),
        base_prefix=str(candidate / "runtime"), flags=SimpleNamespace(isolated=1),
        dont_write_bytecode=True, modules=sys.modules,
    ))
    original = bootstrap.validate_snapshot
    calls = 0

    def verify_and_replace(path):
        nonlocal calls
        calls += 1
        result = original(path)
        if calls == 2:
            # After the final check, replace the path; the already read bytes
            # must still be executed, never a later import from disk.
            (candidate / "runtime/lib/python3.13/site-packages/device_runtime/management_lock.py").write_text(
                "value = 'replaced'\n"
            )
        return result

    monkeypatch.setattr(bootstrap, "validate_snapshot", verify_and_replace)
    original_package = sys.modules["device_runtime"]
    bootstrap.run_verified_helper_candidate(candidate.parent, "v1", registry="r", socket_path="s", group=7)
    assert sys.modules["device_runtime"] is original_package
    assert snapshot["inventory"] and calls == 2
    assert real_sys is sys


@pytest.mark.parametrize("failure", ["interpreter", "not_isolated", "bytecode", "source_drift"])
def test_verified_helper_candidate_refuses_untrusted_launch(runtime, monkeypatch, failure):
    import hashlib
    import sys
    from types import SimpleNamespace

    source, destination = runtime
    site = source / "lib/python3.13/site-packages/device_runtime"
    site.mkdir(parents=True)
    for name in ("__init__", "management_lock", "native_helper"):
        (site / f"{name}.py").write_text("# fixture\n")
    candidate = destination.parent / "v1"
    bootstrap.snapshot_runtime(source, candidate)
    evidence = {
        "snapshot_sha256": bootstrap._digest(candidate / "snapshot.json"),
        "helper_sha256": hashlib.sha256((site / "native_helper.py").read_bytes()).hexdigest(),
    }
    monkeypatch.setattr(bootstrap, "read_helper_candidate", lambda *_: evidence)
    python = candidate / "runtime/bin/python3.13"
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(
        platform="linux", executable=str(python) if failure != "interpreter" else "/other/python",
        base_prefix=str(candidate / "runtime"), flags=SimpleNamespace(isolated=failure != "not_isolated"),
        dont_write_bytecode=failure != "bytecode", modules=sys.modules,
    ))
    if failure == "source_drift":
        (candidate / "runtime/lib/python3.13/site-packages/device_runtime/native_helper.py").write_text(
            "# changed\n"
        )
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.run_verified_helper_candidate(candidate.parent, "v1", registry="r", socket_path="s", group=7)


def test_transition_observation_is_read_only_and_refuses_drift(runtime, monkeypatch):

    source, destination = runtime
    module = source / "lib/python3.13/site-packages/device_runtime/native_helper.py"
    module.parent.mkdir(parents=True)
    module.write_text("# fixture")
    registry = destination.parent
    candidates = registry / "bootstrap-candidates"
    candidates.mkdir()
    unit_dir = registry / "systemd"
    unit_dir.mkdir()
    unit = unit_dir / "ai-device-native-helper.service"
    unit.write_text("old helper unit")
    monkeypatch.setattr(
        bootstrap,
        "inspect_helper_candidate",
        lambda candidate: {
            "snapshot_sha256": bootstrap._digest(candidate / "snapshot.json"),
            "helper_sha256": bootstrap._digest(
                candidate / "runtime/lib/python3.13/site-packages/device_runtime/native_helper.py"
            ),
        },
    )
    bootstrap.publish_helper_candidate(source, candidates, "v1")
    with bootstrap.helper.package_lease(registry, exclusive=True):
        pass
    before = unit.read_bytes()
    result = bootstrap.inspect_helper_transition(registry, unit_dir, "v1")
    assert result["unit_sha256"] == bootstrap._digest(unit)
    assert not result["switch_allowed"] and not result["process_quiescence_verified"]
    assert not result["protocol_compatibility_verified"] and unit.read_bytes() == before
    (candidates / "v1/candidate.json").write_text("{}")
    with pytest.raises(bootstrap.BootstrapError, match="evidence differs"):
        bootstrap.inspect_helper_transition(registry, unit_dir, "v1")
    assert unit.read_bytes() == before
