import json
from types import SimpleNamespace

import pytest

from device_runtime import native_update_job as job


@pytest.fixture
def transaction(tmp_path, monkeypatch):
    monkeypatch.setattr(job.helper, "_trusted", lambda *a, **kw: None)
    monkeypatch.setattr(job.bootstrap, "validate_snapshot", lambda directory: {})
    stem = tmp_path / "manager/updates/5-probe--one"
    stem.parent.mkdir(parents=True)
    prepared = {"metadata": {"Package": "fixture", "Version": "2", "Architecture": "amd64"}}
    stem.with_suffix(".prepared.json").write_text(json.dumps(prepared))
    digest = job.helper._digest(stem.with_suffix(".prepared.json"))
    stem.with_suffix(".approved.json").write_text(
        json.dumps({"prepared_sha256": digest, "approved_by_uid": 0})
    )
    snapshot = stem.with_suffix(".bootstrap")
    snapshot.mkdir()
    (snapshot / "snapshot.json").write_text("snapshot fixture")
    intent = {
        "prepared_sha256": digest,
        "runtime_snapshot_sha256": job.helper._digest(snapshot / "snapshot.json"),
        "target_package": {"name": "fixture", "version": "2", "architecture": "amd64"},
    }
    stem.with_suffix(".apply-intent.json").write_text(json.dumps(intent))
    return stem


def test_staged_job_is_deterministic_non_dispatching(transaction):
    first = job.describe_job(transaction)
    assert job.describe_job(transaction) == first
    descriptor = first["descriptor"]
    assert descriptor["dispatch_allowed"] is False
    assert descriptor["state"] == "staged_not_dispatchable"
    content = first["unit_content"]
    assert "Restart=no" in content and "[Install]" not in content
    assert "TimeoutStartSec=infinity" in content
    assert f"ExecStart={descriptor['python']} -I -B {descriptor['worker']}" in content
    assert "ConditionPathExists=" + descriptor["launch_gate"] in content
    assert not transaction.with_suffix(".job").exists()


@pytest.mark.parametrize("suffix", [".prepared.json", ".approved.json", ".apply-intent.json"])
@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_describe_job_rejects_nonregular_evidence(transaction, suffix, kind):
    import os

    path = transaction.with_suffix(suffix)
    path.unlink()
    if kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    with pytest.raises(job.JobError, match="job evidence"):
        job.describe_job(transaction)
    assert os.path.lexists(path)


def test_describe_job_rejects_nested_target_type_drift(transaction):
    prepared_path = transaction.with_suffix(".prepared.json")
    approval_path = transaction.with_suffix(".approved.json")
    intent_path = transaction.with_suffix(".apply-intent.json")
    prepared = json.loads(prepared_path.read_bytes())
    prepared["metadata"]["Version"] = 1
    prepared_path.write_text(json.dumps(prepared))
    digest = job.helper._digest(prepared_path)
    approval_path.write_text(json.dumps({"prepared_sha256": digest, "approved_by_uid": 0}))
    intent = json.loads(intent_path.read_bytes())
    intent["prepared_sha256"] = digest
    intent["target_package"]["version"] = True
    intent_path.write_text(json.dumps(intent))
    before = {path: path.read_bytes() for path in (prepared_path, approval_path, intent_path)}
    with pytest.raises(job.JobError, match="job intent differs"):
        job.describe_job(transaction)
    assert {path: path.read_bytes() for path in before} == before
    assert not transaction.with_suffix(".job").exists()


@pytest.mark.parametrize("change", ["approval", "target", "snapshot"])
def test_changed_transaction_evidence_refused(transaction, change):
    if change == "approval":
        transaction.with_suffix(".approved.json").write_text("{}")
    elif change == "target":
        path = transaction.with_suffix(".apply-intent.json")
        intent = json.loads(path.read_bytes())
        intent["target_package"]["version"] = "other"
        path.write_text(json.dumps(intent))
    else:
        (transaction.with_suffix(".bootstrap") / "snapshot.json").write_text("changed")
    with pytest.raises(job.JobError, match="approval|intent"):
        job.describe_job(transaction)


def test_dispatch_never_waits_for_the_package_transaction(transaction, monkeypatch):
    """Dispatch is a submission; the 30s runner budget must not bound execution.

    A blocking systemd-run against a oneshot with TimeoutStartSec=infinity would
    hold the caller for the whole transaction and then be cut off by the timeout,
    recording dispatch_unknown while the unit was still running.
    """
    from types import SimpleNamespace

    from device_runtime import native_update as update

    def prepared(binding, stem):
        return None

    from contextlib import contextmanager

    @contextmanager
    def txn(instance_id, operation_id, **kwargs):
        yield SimpleNamespace(), transaction

    @contextmanager
    def lease(*args, **kwargs):
        yield None

    manager = SimpleNamespace(
        packages=update.Debian(),
        registry=transaction.parent.parent,
        _transaction=txn,
        _dispatch_transaction=txn,
        _prepared=prepared,
        _apply_lease=lease,
    )
    seen = {}

    def runner(argv, **kw):
        seen["timeout"] = kw.get("timeout")
        seen["no_block"] = "--no-block" in argv

    monkeypatch.setattr(job, "_require_proven_job_fence", lambda: None)
    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    # The heavy snapshot preparation is unrelated to this contract.
    monkeypatch.setattr(job, "_prepare_dispatch_locked", lambda *a, **k: (
        None,
        (["/usr/bin/systemd-run", "--no-block", "--unit=x.service", "--", "worker"], "d" * 64, "x.service"),
    ))
    receipt = job.dispatch_job(manager, "probe", "one", runner=runner)
    assert receipt["state"] == "dispatch_accepted"
    assert seen["no_block"] is True
    # The timeout bounds the submission call only, never the package transaction.
    assert seen["timeout"] == 30


@pytest.mark.parametrize("lost", [False, True])
def test_dispatch_records_once_without_replay(transaction, monkeypatch, lost):
    import subprocess
    from contextlib import contextmanager
    from types import SimpleNamespace

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    # Static fixture already has apply intent; dispatch must begin before apply.
    transaction.with_suffix(".apply-intent.json").unlink()
    monkeypatch.setattr(job.bootstrap, "snapshot_runtime", lambda *a: {})
    monkeypatch.setattr(job, "_require_matching_worker_python_sources", lambda *a: None)
    real_digest = job.helper._digest

    def digest(path):
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py", "management_lock.py"):
            return "same-worker-bytes"
        return real_digest(path)

    monkeypatch.setattr(job.helper, "_digest", digest)

    held = []

    @contextmanager
    def lease(*a):
        assert held == ["instance"]
        held.append("package")
        try:
            yield
        finally:
            assert held.pop() == "package"

    @contextmanager
    def txn(*a, **kwargs):
        held.append("instance")
        try:
            yield {}, transaction
        finally:
            assert held.pop() == "instance"

    def prepared(*a):
        assert held == ["instance"]
        return {}

    manager = SimpleNamespace(
        packages=update.Debian(),
        registry=transaction.parent.parent,
        _transaction=txn, _dispatch_transaction=txn,
        _prepared=prepared,
        _apply_lease=lease,
    )
    calls = []

    def runner(argv, **kw):
        assert not held, "release instance and package leases before independent worker starts"
        calls.append(argv)
        if lost:
            raise subprocess.TimeoutExpired(argv, 30)

    # Test the dormant dispatcher mechanics only; production gate is tested separately.
    monkeypatch.setattr(job, "_require_proven_job_fence", lambda: None)
    first = job.dispatch_job(manager, "probe", "one", runner=runner)
    assert first["state"] == ("dispatch_unknown" if lost else "dispatch_accepted")
    assert first["package_execution_confirmed"] is False
    # Accepted and unknown dispatch both leave a durable start fence.
    with pytest.raises(job.helper.HelperError, match="package transaction blocks start"):
        job.helper._assert_no_package_intent(manager.registry)
    second = job.dispatch_job(manager, "probe", "one", runner=runner)
    assert second["state"] == "dispatch_already_recorded" and not second["replayed"]
    assert len(calls) == 1
    assert calls[0][0] == "/usr/bin/systemd-run"
    assert "--no-block" in calls[0]
    unit_at = next(i for i, a in enumerate(calls[0]) if a.startswith("--unit="))
    assert calls[0].index("--no-block") < unit_at
    assert "--property=Restart=no" in calls[0]
    assert "--property=RemainAfterExit=yes" in calls[0]


def test_locked_dispatch_preparation_remains_default_deny(transaction, monkeypatch):
    from types import SimpleNamespace

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    manager = SimpleNamespace(
        packages=update.Debian(),
        registry=transaction.parent.parent,
        _prepared=lambda *args: pytest.fail("metadata preparation before default deny"),
        _apply_lease=lambda *args: pytest.fail("package lease before default deny"),
    )
    before = {p: p.read_bytes() for p in transaction.parent.iterdir() if p.is_file()}
    with pytest.raises(job.JobError, match="new dispatch disabled"):
        job._prepare_dispatch_locked(manager, "probe", "one", {}, transaction)
    assert {p: p.read_bytes() for p in before} == before
    assert not transaction.with_suffix(".job-intent.json").exists()


def test_dangling_job_intent_refused_before_metadata(transaction, monkeypatch):
    from types import SimpleNamespace

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    marker = transaction.with_suffix(".job-intent.json")
    marker.symlink_to("missing")
    manager = SimpleNamespace(
        packages=update.Debian(),
        registry=transaction.parent.parent,
        _prepared=lambda *args: pytest.fail("metadata read before invalid intent rejection"),
    )
    with pytest.raises(job.JobError, match="invalid job intent path"):
        job._prepare_dispatch_locked(manager, "probe", "one", {}, transaction)
    assert marker.is_symlink()


def test_locked_dispatch_preparation_does_not_reenter_transaction_or_run_job(transaction, monkeypatch):
    import subprocess
    from contextlib import contextmanager
    from types import SimpleNamespace

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    transaction.with_suffix(".apply-intent.json").unlink()
    monkeypatch.setattr(job, "_require_proven_job_fence", lambda: None)
    monkeypatch.setattr(job.bootstrap, "snapshot_runtime", lambda *args: {})
    monkeypatch.setattr(job, "_require_matching_worker_python_sources", lambda *args: None)
    real_digest = job.helper._digest

    def digest(path):
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py", "management_lock.py"):
            return "same-worker-bytes"
        return real_digest(path)

    monkeypatch.setattr(job.helper, "_digest", digest)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("job ran inside lease"))
    events = []

    @contextmanager
    def lease(*args):
        events.append("package acquired")
        try:
            yield
        finally:
            events.append("package released")

    manager = SimpleNamespace(
        packages=update.Debian(),
        registry=transaction.parent.parent,
        _transaction=lambda *args: pytest.fail("instance transaction reacquired"),
        _prepared=lambda *args: events.append("prepared checked"),
        _apply_lease=lease,
    )
    existing, prepared = job._prepare_dispatch_locked(manager, "probe", "one", {}, transaction)
    assert existing is None
    argv, digest_value, unit = prepared
    assert argv[0] == "/usr/bin/systemd-run"
    assert digest_value == real_digest(transaction.with_suffix(".job-intent.json"))
    assert "--unit=" + unit in argv
    assert events == ["prepared checked", "package acquired", "package released"]
    recorded, retry = job._prepare_dispatch_locked(manager, "probe", "one", {}, transaction)
    assert recorded["state"] == "dispatch_already_recorded"
    assert recorded["replayed"] is False and retry is None
    assert events == ["prepared checked", "package acquired", "package released", "prepared checked"]


def test_locked_dispatch_preparation_uses_real_instance_and_package_leases(transaction, monkeypatch):
    import subprocess

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    transaction.with_suffix(".apply-intent.json").unlink()
    registry = transaction.parent.parent
    manager = update.Updates(registry, packages=update.Debian())
    monkeypatch.setattr(manager.helper, "_binding", lambda _: {})
    monkeypatch.setattr(manager, "_prepared", lambda *args: None)
    monkeypatch.setattr(job, "_require_proven_job_fence", lambda: None)
    monkeypatch.setattr(job.bootstrap, "snapshot_runtime", lambda *args: {})
    monkeypatch.setattr(job, "_require_matching_worker_python_sources", lambda *args: None)
    real_digest = job.helper._digest

    def digest(path):
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py", "management_lock.py"):
            return "same-worker-bytes"
        return real_digest(path)

    monkeypatch.setattr(job.helper, "_digest", digest)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("external process ran"))
    real_save = job.helper._save_new

    def save_with_competitors(path, value):
        with pytest.raises(job.helper.HelperError, match="instance management busy"), manager.helper._lease("probe"):
            pass
        with (
            pytest.raises(job.helper.HelperError, match="package or start sender still executing"),
            job.helper.package_lease(registry),
        ):
            pass
        real_save(path, value)

    monkeypatch.setattr(job.helper, "_save_new", save_with_competitors)
    with manager.helper._lease("probe"):
        binding, stem = manager._transaction_locked("probe", "one")
        assert stem == transaction
        existing, prepared = job._prepare_dispatch_locked(manager, "probe", "one", binding, stem)
        assert existing is None and prepared is not None
        with pytest.raises(job.helper.HelperError, match="instance management busy"), manager.helper._lease("probe"):
            pass
    with manager.helper._lease("probe"), job.helper.package_lease(registry):
        assert transaction.with_suffix(".job-intent.json").is_file()


@pytest.mark.parametrize("failure_stage", ["file", "directory"])
def test_dispatch_fsync_failure_never_calls_runner(transaction, monkeypatch, failure_stage):
    import os
    import stat
    from contextlib import contextmanager
    from types import SimpleNamespace

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    monkeypatch.setattr(job, "_require_proven_job_fence", lambda: None)
    transaction.with_suffix(".apply-intent.json").unlink()
    monkeypatch.setattr(job.bootstrap, "snapshot_runtime", lambda *args: {})
    monkeypatch.setattr(job, "_require_matching_worker_python_sources", lambda *a: None)
    original_digest = job.helper._digest

    def digest(path):
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py", "management_lock.py"):
            return "same-worker-bytes"
        return original_digest(path)

    monkeypatch.setattr(job.helper, "_digest", digest)

    @contextmanager
    def txn(*args, **kwargs):
        yield {}, transaction

    @contextmanager
    def lease(*args):
        yield

    manager = SimpleNamespace(
        packages=update.Debian(), registry=transaction.parent.parent,
        _transaction=txn, _dispatch_transaction=txn, _prepared=lambda *args: {}, _apply_lease=lease,
    )
    original_fsync = os.fsync

    def fail_fsync(fd):
        is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if is_directory == (failure_stage == "directory"):
            raise OSError("injected fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(job.helper.os, "fsync", fail_fsync)
    calls = []
    with pytest.raises(OSError, match="injected fsync failure"):
        job.dispatch_job(manager, "probe", "one", runner=lambda *args, **kwargs: calls.append(args))
    assert not calls
    intent = transaction.with_suffix(".job-intent.json")
    assert intent.exists() is (failure_stage == "directory")
    if intent.exists():
        result = job.dispatch_job(manager, "probe", "one", runner=lambda *args, **kwargs: calls.append(args))
        assert result["state"] == "dispatch_already_recorded"
        assert result["replayed"] is False
        assert not calls


def test_dispatch_process_exit_after_durable_intent_does_not_replay(transaction, monkeypatch):
    import os
    import subprocess
    import sys
    from contextlib import contextmanager
    from types import SimpleNamespace

    from device_runtime import native_update as update

    if not hasattr(os, "fork"):
        pytest.skip("requires fork")
    if os.environ.get("DEVICE_CRASH_TEST_CHILD") != "1":
        env = {**os.environ, "DEVICE_CRASH_TEST_CHILD": "1"}
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"{__file__}::test_dispatch_process_exit_after_durable_intent_does_not_replay"],
            check=True, timeout=30, env=env,
        )
        return
    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    monkeypatch.setattr(job, "_require_proven_job_fence", lambda: None)
    transaction.with_suffix(".apply-intent.json").unlink()
    monkeypatch.setattr(job.bootstrap, "snapshot_runtime", lambda *args: {})
    monkeypatch.setattr(job, "_require_matching_worker_python_sources", lambda *a: None)
    original_digest = job.helper._digest
    original_save = job.helper._save_new
    parent_pid = os.getpid()
    runner_marker = transaction.parent / "unexpected-runner"

    def digest(path):
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py", "management_lock.py"):
            return "same-worker-bytes"
        return original_digest(path)

    def save_and_exit(path, value, **kwargs):
        original_save(path, value, **kwargs)
        if os.getpid() != parent_pid and path.name.endswith(".job-intent.json"):
            os._exit(73)

    def unexpected_runner(*args, **kwargs):
        runner_marker.write_text("called")
        raise AssertionError("runner must not execute")

    @contextmanager
    def txn(*args, **kwargs):
        yield {}, transaction

    @contextmanager
    def lease(*args):
        yield

    monkeypatch.setattr(job.helper, "_digest", digest)
    monkeypatch.setattr(job.helper, "_save_new", save_and_exit)
    manager = SimpleNamespace(
        packages=update.Debian(), registry=transaction.parent.parent,
        _transaction=txn, _dispatch_transaction=txn, _prepared=lambda *args: {}, _apply_lease=lease,
    )
    child = os.fork()
    if child == 0:
        try:
            job.dispatch_job(manager, "probe", "one", runner=unexpected_runner)
        finally:
            os._exit(74)
    _, status = os.waitpid(child, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 73
    assert transaction.with_suffix(".job-intent.json").exists()
    assert not runner_marker.exists()
    result = job.dispatch_job(manager, "probe", "one", runner=unexpected_runner)
    assert result["state"] == "dispatch_already_recorded" and result["replayed"] is False
    assert not runner_marker.exists()


@pytest.mark.parametrize(
    "change",
    ["instance_id", "operation_id", "unit", "prepared_sha256", "runtime_snapshot_sha256",
     "schema_version", "close_protocol", "state", "extra", "not_object", "prepared", "snapshot"],
)
def test_recorded_dispatch_rejects_identity_or_evidence_drift(transaction, monkeypatch, change):
    import hashlib
    from contextlib import contextmanager
    from types import SimpleNamespace

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    snapshot = transaction.with_suffix(".bootstrap") / "snapshot.json"
    prepared = transaction.with_suffix(".prepared.json")
    intent = {
        "schema_version": 1,
        "state": "job_dispatch_intent",
        "close_protocol": "package-lease-v1",
        "unit": "ai-device-package-" + hashlib.sha256(str(transaction).encode()).hexdigest()
        + ".service",
        "instance_id": "probe",
        "operation_id": "one",
        "prepared_sha256": job.helper._digest(prepared),
        "runtime_snapshot_sha256": job.helper._digest(snapshot),
    }
    if change == "not_object":
        intent = []
    elif change == "schema_version":
        intent[change] = True  # bool must not stand in for the integer schema version.
    elif change == "extra":
        intent["extra"] = "unexpected"
    elif change == "prepared":
        prepared.write_text("changed prepared")
    elif change == "snapshot":
        snapshot.write_text("changed snapshot")
    else:
        intent[change] = "changed"
    path = transaction.with_suffix(".job-intent.json")
    path.write_text(json.dumps(intent))
    before = path.read_bytes()

    @contextmanager
    def txn(*args, **kwargs):
        yield {}, transaction

    manager = SimpleNamespace(packages=update.Debian(), _transaction=txn, _dispatch_transaction=txn, _prepared=lambda *a: {})
    calls = []
    with pytest.raises(job.JobError, match="recorded job intent differs"):
        job.dispatch_job(manager, "probe", "one", runner=lambda *a, **kw: calls.append(a))
    assert path.read_bytes() == before and not calls


def test_dispatch_process_exit_after_runner_begins_stays_recorded(transaction, monkeypatch):
    import os
    import subprocess
    import sys
    from contextlib import contextmanager
    from types import SimpleNamespace

    from device_runtime import native_update as update

    if not hasattr(os, "fork"):
        pytest.skip("requires fork")
    if os.environ.get("DEVICE_CRASH_TEST_CHILD") != "1":
        env = {**os.environ, "DEVICE_CRASH_TEST_CHILD": "1"}
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"{__file__}::test_dispatch_process_exit_after_runner_begins_stays_recorded"],
            check=True, timeout=30, env=env,
        )
        return
    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(job, "_require_proven_job_fence", lambda: None)
    transaction.with_suffix(".apply-intent.json").unlink()
    monkeypatch.setattr(job.bootstrap, "snapshot_runtime", lambda *args: {})
    monkeypatch.setattr(job, "_require_matching_worker_python_sources", lambda *a: None)
    original_digest = job.helper._digest

    def digest(path):
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py", "management_lock.py"):
            return "same-worker-bytes"
        return original_digest(path)

    @contextmanager
    def txn(*args, **kwargs):
        yield {}, transaction

    @contextmanager
    def lease(*args):
        yield

    monkeypatch.setattr(job.helper, "_digest", digest)
    manager = SimpleNamespace(
        packages=update.Debian(), registry=transaction.parent.parent,
        _transaction=txn, _dispatch_transaction=txn, _prepared=lambda *args: {}, _apply_lease=lease,
    )
    marker = transaction.parent / "runner-started-test-marker"
    parent_pid = os.getpid()

    def runner_then_exit(*args, **kwargs):
        if os.getpid() == parent_pid:
            raise AssertionError("recorded job must not replay")
        marker.write_text("started")
        os._exit(73)

    child = os.fork()
    if child == 0:
        try:
            job.dispatch_job(manager, "probe", "one", runner=runner_then_exit)
        finally:
            os._exit(74)
    _, status = os.waitpid(child, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 73
    assert marker.read_text() == "started"
    assert transaction.with_suffix(".job-intent.json").exists()
    assert not transaction.with_suffix(".job-dispatch.json").exists()
    result = job.dispatch_job(manager, "probe", "one", runner=runner_then_exit)
    assert result["state"] == "dispatch_already_recorded" and result["replayed"] is False
    assert marker.read_text() == "started"


def test_dispatch_rejected_by_real_shared_start_sender_lock(transaction, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    monkeypatch.setattr(job, "_require_proven_job_fence", lambda: None)
    transaction.with_suffix(".apply-intent.json").unlink()
    registry = transaction.parent.parent

    @contextmanager
    def txn(*args, **kwargs):
        yield {}, transaction

    @contextmanager
    def lease(*args):
        with job.helper.package_lease(registry, exclusive=True) as fd:
            yield fd

    manager = SimpleNamespace(
        packages=update.Debian(), registry=registry, _transaction=txn, _dispatch_transaction=txn,
        _prepared=lambda *args: {}, _apply_lease=lease,
    )
    calls = []
    with (
        job.helper.package_lease(registry),
        pytest.raises(job.helper.HelperError, match="still executing"),
    ):
        job.dispatch_job(manager, "probe", "one", runner=lambda *a, **k: calls.append(a))
    assert not transaction.with_suffix(".job-intent.json").exists()
    assert not calls


@pytest.mark.parametrize(
    "unit_state,expected",
    [
        ("active", "job_in_progress"),
        ("failed", "job_failed_requires_review"),
        ("inactive", "dispatch_unknown"),
    ],
)
def test_observe_read_only_and_never_grants_retry(transaction, unit_state, expected):
    import hashlib
    from types import SimpleNamespace

    intent = {
        "unit": "ai-device-package-"
        + hashlib.sha256(str(transaction).encode()).hexdigest()
        + ".service",
        "instance_id": "probe",
        "operation_id": "one",
        "prepared_sha256": job.helper._digest(transaction.with_suffix(".prepared.json")),
        "runtime_snapshot_sha256": "snapshot",
    }
    transaction.with_suffix(".job-intent.json").write_text(json.dumps(intent))
    transaction.with_suffix(".apply-intent.json").unlink()
    manager = SimpleNamespace(registry=transaction.parent.parent)
    before = {
        str(p.relative_to(manager.registry)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in manager.registry.rglob("*")
        if p.is_file()
    }

    def runner(argv, **kw):
        assert argv[:2] == ["/usr/bin/systemctl", "show"]
        assert "--all" in argv and "--no-pager" in argv
        return SimpleNamespace(
            stdout=f"LoadState=loaded\nActiveState={unit_state}\nSubState=dead\nResult=success\nMainPID=0\nJob=\n"
        )

    result = job.observe_job(manager, "probe", "one", runner=runner)
    assert result["state"] == expected and result["retry_allowed"] is False
    assert result["fence_retained"] and not result["apply_intent_present"]
    after = {
        str(p.relative_to(manager.registry)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in manager.registry.rglob("*")
        if p.is_file()
    }
    assert before == after
    assert not list(manager.registry.glob("*.lock"))


@pytest.mark.parametrize("evidence", ["sender", "outcome"])
def test_observe_rejects_execution_evidence_without_apply_intent(transaction, evidence):
    import hashlib
    from types import SimpleNamespace

    intent = {
        "unit": "ai-device-package-" + hashlib.sha256(str(transaction).encode()).hexdigest() + ".service",
        "instance_id": "probe", "operation_id": "one",
        "prepared_sha256": job.helper._digest(transaction.with_suffix(".prepared.json")),
        "runtime_snapshot_sha256": "snapshot",
    }
    transaction.with_suffix(".job-intent.json").write_text(json.dumps(intent))
    transaction.with_suffix(".apply-intent.json").unlink()
    orphan = transaction.with_suffix(f".{evidence}.json")
    orphan.write_text('{"state":"fixture"}')
    manager = SimpleNamespace(registry=transaction.parent.parent)
    before = {p.name: p.read_bytes() for p in transaction.parent.iterdir() if p.is_file()}
    with pytest.raises(job.JobError, match="execution evidence without apply intent"):
        job.observe_job(manager, "probe", "one", runner=lambda *a, **kw: SimpleNamespace(stdout=""))
    assert {p.name: p.read_bytes() for p in transaction.parent.iterdir() if p.is_file()} == before


def test_observe_requires_matching_sender_before_reporting_outcome(transaction):
    import hashlib
    from types import SimpleNamespace

    apply_intent = json.loads(transaction.with_suffix(".apply-intent.json").read_bytes())
    intent = {
        "unit": "ai-device-package-"
        + hashlib.sha256(str(transaction).encode()).hexdigest()
        + ".service",
        "instance_id": "probe",
        "operation_id": "one",
        "prepared_sha256": apply_intent["prepared_sha256"],
        "runtime_snapshot_sha256": apply_intent["runtime_snapshot_sha256"],
    }
    transaction.with_suffix(".job-intent.json").write_text(json.dumps(intent))
    outcome = {
        "intent": apply_intent,
        "state": "package_applied_pending_activation",
        "package_observation": {
            "status": "install ok installed",
            "version": "2",
            "architecture": "amd64",
        },
    }
    transaction.with_suffix(".outcome.json").write_text(json.dumps(outcome))
    sender = {"intent": apply_intent, "state": "sender_reaped", "returncode": 1}
    transaction.with_suffix(".sender.json").write_text(json.dumps(sender))
    manager = SimpleNamespace(registry=transaction.parent.parent)
    runner = lambda *a, **kw: SimpleNamespace(stdout="LoadState=loaded\nActiveState=inactive\n")
    with pytest.raises(job.JobError, match="success evidence"):
        job.observe_job(manager, "probe", "one", runner=runner)
    sender["returncode"] = 0
    transaction.with_suffix(".sender.json").write_text(json.dumps(sender))
    assert (
        job.observe_job(manager, "probe", "one", runner=runner)["state"]
        == "package_outcome_recorded"
    )


def test_observe_missing_journal_never_initializes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(job.helper, "_trusted", lambda *a, **kw: None)
    registry = tmp_path / "manager"
    registry.mkdir()
    with pytest.raises(job.JobError, match="job intent evidence missing"):
        job.observe_job(SimpleNamespace(registry=registry), "probe", "one")
    assert list(registry.iterdir()) == []


def test_observe_query_timeout_keeps_unknown(transaction):
    import hashlib
    import subprocess
    from types import SimpleNamespace

    intent = {
        "unit": "ai-device-package-"
        + hashlib.sha256(str(transaction).encode()).hexdigest()
        + ".service",
        "instance_id": "probe",
        "operation_id": "one",
        "prepared_sha256": job.helper._digest(transaction.with_suffix(".prepared.json")),
    }
    transaction.with_suffix(".job-intent.json").write_text(json.dumps(intent))
    transaction.with_suffix(".apply-intent.json").unlink()

    def runner(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 15)

    result = job.observe_job(
        SimpleNamespace(registry=transaction.parent.parent), "probe", "one", runner=runner
    )
    assert result["state"] == "dispatch_unknown" and not result["retry_allowed"]


def test_new_dispatch_fails_closed_without_cross_version_fence(transaction, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    transaction.with_suffix(".apply-intent.json").unlink()

    @contextmanager
    def txn(*args, **kwargs):
        yield {}, transaction

    manager = SimpleNamespace(
        packages=update.Debian(),
        registry=transaction.parent.parent,
        _transaction=txn, _dispatch_transaction=txn,
        _prepared=lambda *args: {},
    )
    calls = []
    with pytest.raises(job.JobError, match="compatibility unproven"):
        job.dispatch_job(manager, "probe", "one", runner=lambda *a, **k: calls.append(a))
    assert not calls
    assert not transaction.with_suffix(".job-intent.json").exists()
    assert not transaction.with_suffix(".job-dispatch.json").exists()


def test_existing_job_observation_survives_dispatch_gate(transaction, monkeypatch):
    import hashlib
    from contextlib import contextmanager
    from types import SimpleNamespace

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    intent = {
        "schema_version": 1,
        "state": "job_dispatch_intent",
        "close_protocol": "package-lease-v1",
        "unit": "ai-device-package-" + hashlib.sha256(str(transaction).encode()).hexdigest()
        + ".service",
        "instance_id": "probe",
        "operation_id": "one",
        "prepared_sha256": job.helper._digest(transaction.with_suffix(".prepared.json")),
        "runtime_snapshot_sha256": job.helper._digest(
            transaction.with_suffix(".bootstrap") / "snapshot.json"
        ),
    }
    transaction.with_suffix(".job-intent.json").write_text(json.dumps(intent))

    @contextmanager
    def txn(*args, **kwargs):
        yield {}, transaction

    manager = SimpleNamespace(
        packages=update.Debian(),
        registry=transaction.parent.parent,
        _transaction=txn, _dispatch_transaction=txn,
        _prepared=lambda *args: {},
    )
    result = job.dispatch_job(
        manager, "probe", "one", runner=lambda *a, **k: pytest.fail("replayed")
    )
    assert result["state"] == "dispatch_already_recorded" and not result["replayed"]


@pytest.mark.parametrize("changed_file", ["sender", "outcome", "apply", "package_observation"])
def test_observe_rejects_json_type_drift_in_nested_intent(transaction, changed_file):
    import copy
    import hashlib
    from types import SimpleNamespace

    prepared_path = transaction.with_suffix(".prepared.json")
    prepared = json.loads(prepared_path.read_bytes())
    # Synthetic JSON identity exposes bool/int equivalence; no package is run.
    prepared["metadata"]["Version"] = 1
    prepared_path.write_text(json.dumps(prepared))
    digest = job.helper._digest(prepared_path)
    transaction.with_suffix(".approved.json").write_text(
        json.dumps({"prepared_sha256": digest, "approved_by_uid": 0})
    )
    apply_intent = json.loads(transaction.with_suffix(".apply-intent.json").read_bytes())
    apply_intent["prepared_sha256"] = digest
    apply_intent["target_package"]["version"] = 1
    transaction.with_suffix(".apply-intent.json").write_text(json.dumps(apply_intent))
    intent = {
        "unit": "ai-device-package-" + hashlib.sha256(str(transaction).encode()).hexdigest() + ".service",
        "instance_id": "probe", "operation_id": "one", "prepared_sha256": digest,
        "runtime_snapshot_sha256": apply_intent["runtime_snapshot_sha256"],
    }
    transaction.with_suffix(".job-intent.json").write_text(json.dumps(intent))
    sender = {"intent": copy.deepcopy(apply_intent), "state": "sender_reaped", "returncode": 0}
    outcome = {"intent": copy.deepcopy(apply_intent), "state": "unknown", "package_observation": None}
    for name, value in (("sender", sender), ("outcome", outcome)):
        transaction.with_suffix(f".{name}.json").write_text(json.dumps(value))
    manager = SimpleNamespace(registry=transaction.parent.parent)
    runner = lambda *a, **kw: SimpleNamespace(stdout="LoadState=loaded\nActiveState=inactive\n")
    assert job.observe_job(manager, "probe", "one", runner=runner)["state"] == "package_outcome_recorded"
    if changed_file == "apply":
        apply_intent["target_package"]["version"] = True
        transaction.with_suffix(".apply-intent.json").write_text(json.dumps(apply_intent))
        error = "job apply evidence differs"
    elif changed_file == "package_observation":
        outcome["state"] = "package_applied_pending_activation"
        outcome["package_observation"] = {
            "status": "install ok installed", "version": True, "architecture": "amd64"
        }
        transaction.with_suffix(".outcome.json").write_text(json.dumps(outcome))
        error = "job success evidence differs"
    else:
        changed = sender if changed_file == "sender" else outcome
        changed["intent"]["target_package"]["version"] = True
        transaction.with_suffix(f".{changed_file}.json").write_text(json.dumps(changed))
        error = "job outcome evidence differs"
    before = {p.name: p.read_bytes() for p in transaction.parent.iterdir() if p.is_file()}
    with pytest.raises(job.JobError, match=error):
        job.observe_job(manager, "probe", "one", runner=runner)
    assert {p.name: p.read_bytes() for p in transaction.parent.iterdir() if p.is_file()} == before


def test_dispatch_refuses_drift_in_snapshot_management_lock_before_intent(transaction, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    monkeypatch.setattr(job, "_require_proven_job_fence", lambda: None)
    monkeypatch.setattr(job.bootstrap, "snapshot_runtime", lambda *args: {})
    transaction.with_suffix(".apply-intent.json").unlink()
    original_digest = job.helper._digest
    snapshot = transaction.with_suffix(".bootstrap")

    def digest(path):
        path = __import__("pathlib").Path(path)
        if path.name == "management_lock.py":
            return "old" if snapshot in path.parents else "current"
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py"):
            return "same-worker-bytes"
        return original_digest(path)

    monkeypatch.setattr(job.helper, "_digest", digest)

    @contextmanager
    def txn(*args, **kwargs):
        yield {}, transaction

    @contextmanager
    def lease(*args):
        yield

    manager = SimpleNamespace(
        packages=update.Debian(), registry=transaction.parent.parent,
        _transaction=txn, _dispatch_transaction=txn, _prepared=lambda *args: {}, _apply_lease=lease,
    )
    calls = []
    with pytest.raises(job.JobError, match="snapshot worker code differs"):
        job.dispatch_job(manager, "probe", "one", runner=lambda *a, **k: calls.append(a))
    assert not calls
    assert not transaction.with_suffix(".job-intent.json").exists()
    assert not transaction.with_suffix(".job-dispatch.json").exists()


@pytest.mark.parametrize("change", ["contents", "missing", "extra", "symlink", "nested-symlink", "pyc-contents", "pyc-extra", "pyc-missing", "pyc-symlink"])
def test_worker_python_source_tree_rejects_drift(tmp_path, change):
    import shutil

    current = tmp_path / "current"
    current.mkdir()
    (current / "__init__.py").write_text("version = 1\n")
    (current / "guard.py").write_text("value = 1\n")
    (current / "__pycache__").mkdir()
    (current / "__pycache__" / "guard.pyc").write_bytes(b"bytecode")
    snapshot = tmp_path / "snapshot"
    shutil.copytree(current, snapshot)
    job._require_matching_worker_python_sources(snapshot, current)
    if change == "contents":
        (snapshot / "guard.py").write_text("value = 2\n")
    elif change == "missing":
        (snapshot / "guard.py").unlink()
    elif change == "extra":
        (snapshot / "extra.py").write_text("value = 1\n")
    elif change == "symlink":
        (snapshot / "guard.py").unlink()
        (snapshot / "guard.py").symlink_to("__init__.py")
    elif change == "nested-symlink":
        (snapshot / "nested").symlink_to(current, target_is_directory=True)
    elif change == "pyc-contents":
        (snapshot / "__pycache__" / "guard.pyc").write_bytes(b"other")
    elif change == "pyc-extra":
        (snapshot / "__pycache__" / "extra.pyc").write_bytes(b"other")
    elif change == "pyc-missing":
        (snapshot / "__pycache__" / "guard.pyc").unlink()
    else:
        (snapshot / "__pycache__" / "guard.pyc").unlink()
        (snapshot / "__pycache__" / "guard.pyc").symlink_to("missing")
    with pytest.raises(job.JobError, match="snapshot worker Python source tree"):
        job._require_matching_worker_python_sources(snapshot, current)


def test_worker_python_source_tree_compares_bytecode_cache(tmp_path):
    import shutil

    current = tmp_path / "current"
    current.mkdir()
    (current / "__init__.py").write_text("")
    snapshot = tmp_path / "snapshot"
    shutil.copytree(current, snapshot)
    (snapshot / "__pycache__").mkdir()
    (snapshot / "__pycache__" / "__init__.pyc").write_bytes(b"fixture")
    with pytest.raises(job.JobError, match="snapshot worker Python source tree differs"):
        job._require_matching_worker_python_sources(snapshot, current)

def test_dispatch_rejects_other_snapshot_source_drift_before_intent(transaction, monkeypatch):
    import shutil
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    monkeypatch.setattr(job, "_require_proven_job_fence", lambda: None)
    monkeypatch.setattr(job.bootstrap, "snapshot_runtime", lambda *args: {})
    transaction.with_suffix(".apply-intent.json").unlink()
    package = transaction.with_suffix(".bootstrap") / "runtime/lib/python3.13/site-packages/device_runtime"
    shutil.copytree(Path(job.__file__).parent, package)
    (package / "native_candidate.py").write_text("# stale snapshot\n")

    @contextmanager
    def txn(*args, **kwargs):
        yield {}, transaction

    @contextmanager
    def lease(*args):
        yield

    manager = SimpleNamespace(
        packages=update.Debian(), registry=transaction.parent.parent,
        _transaction=txn, _dispatch_transaction=txn, _prepared=lambda *args: {}, _apply_lease=lease,
    )
    calls = []
    with pytest.raises(job.JobError, match="snapshot worker Python source tree differs"):
        job.dispatch_job(manager, "probe", "one", runner=lambda *a, **k: calls.append(a))
    assert not calls and not transaction.with_suffix(".job-intent.json").exists()
    assert not transaction.with_suffix(".job-dispatch.json").exists()


def test_dispatch_default_denied_before_update_directory_or_binding(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from device_runtime import native_update as update

    monkeypatch.setattr(job.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(__import__('sys'), 'platform', 'linux')
    monkeypatch.setattr(job.helper, '_trusted', lambda *a, **kw: None)
    registry = tmp_path / 'registry'
    registry.mkdir()

    class Helper:
        @contextmanager
        def _lease(self, instance_id):
            yield

        def _binding(self, instance_id):
            pytest.fail('binding read before new dispatch was denied')

    manager = update.Updates(registry, packages=update.Debian(), helper=Helper())
    with pytest.raises(job.JobError, match='new dispatch disabled'):
        job.dispatch_job(manager, 'probe', 'one', runner=lambda *a, **kw: pytest.fail('job ran'))
    assert not (registry / 'updates').exists()




@pytest.mark.parametrize("suffix", [".apply-intent.json", ".outcome.json", ".sender.json"])
@pytest.mark.parametrize("kind", ["dangling", "symlink", "directory", "fifo"])
def test_observe_job_rejects_nonregular_evidence(tmp_path, monkeypatch, suffix, kind):
    import os
    from types import SimpleNamespace

    monkeypatch.setattr(job.helper, "_trusted", lambda *a, **kw: None)
    directory = tmp_path / "updates"
    directory.mkdir()
    stem = directory / "5-probe--one"
    stem.with_suffix(".job-intent.json").write_text("{}")
    path = stem.with_suffix(suffix)
    path.unlink(missing_ok=True)
    if kind == "dangling":
        path.symlink_to(directory / "missing")
    elif kind == "symlink":
        path.symlink_to(stem.with_suffix(".job-intent.json"))
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    with pytest.raises(job.JobError):
        job.observe_job(
            SimpleNamespace(registry=tmp_path),
            "probe",
            "one",
            runner=lambda *a, **k: pytest.fail("unit query"),
        )
    assert os.path.lexists(path)


def test_observe_job_outcome_without_sender_refused(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(job.helper, "_trusted", lambda *a, **kw: None)
    directory = tmp_path / "updates"
    directory.mkdir()
    stem = directory / "5-probe--one"
    prepared = {"metadata": {"Package": "fixture", "Version": "2", "Architecture": "amd64"}}
    stem.with_suffix(".prepared.json").write_text(json.dumps(prepared))
    digest = job.helper._digest(stem.with_suffix(".prepared.json"))
    stem.with_suffix(".approved.json").write_text(
        json.dumps({"prepared_sha256": digest, "approved_by_uid": 0})
    )
    stem.with_suffix(".job-intent.json").write_text(
        json.dumps(
            {
                "unit": "ai-device-package-"
                + job.hashlib.sha256(str(stem).encode()).hexdigest()
                + ".service",
                "instance_id": "probe",
                "operation_id": "one",
                "prepared_sha256": digest,
                "runtime_snapshot_sha256": "snapshot",
            }
        )
    )
    stem.with_suffix(".apply-intent.json").write_text(
        json.dumps(
            {
                "prepared_sha256": digest,
                "runtime_snapshot_sha256": "snapshot",
                "target_package": {"name": "fixture", "version": "2", "architecture": "amd64"},
            }
        )
    )
    stem.with_suffix(".outcome.json").write_text("{}")
    with pytest.raises(job.JobError, match="lacks sender evidence"):
        job.observe_job(
            SimpleNamespace(registry=tmp_path),
            "probe",
            "one",
            runner=lambda *a, **k: pytest.fail("unit query"),
        )


# --- run-start evidence: proves only that execution began ---


def _job_intent_for(transaction):
    """Write a job intent whose digest the run-start recorder will re-check."""
    stem = transaction
    intent = {
        "schema_version": 1,
        "state": "job_dispatch_intent",
        "close_protocol": "package-lease-v1",
        "unit": "ai-device-package-"
        + __import__("hashlib").sha256(str(stem).encode()).hexdigest()
        + ".service",
        "instance_id": "probe",
        "operation_id": "one",
        "prepared_sha256": job.helper._digest(stem.with_suffix(".prepared.json")),
        "runtime_snapshot_sha256": job.helper._digest(
            stem.with_suffix(".bootstrap") / "snapshot.json"
        ),
    }
    path = stem.with_suffix(".job-intent.json")
    path.write_text(json.dumps(intent))
    return job.helper._digest(path)


def test_run_start_records_once_and_never_overwrites(transaction, monkeypatch):
    monkeypatch.setattr(job, "_boot_id", lambda: "394e9cfa-a8bb-4dce-8775-7a40c4368041")
    digest = _job_intent_for(transaction)
    registry = transaction.parent.parent
    first = job._record_run_start(registry, "probe", "one", digest)
    assert first["state"] == "job_run_started"
    assert first["job_intent_sha256"] == digest
    path = transaction.with_suffix(".job-run-start.json")
    assert json.loads(path.read_text()) == first
    # A second start for the same dispatch is refused, not silently replaced.
    with pytest.raises(job.JobError, match="duplicate start"):
        job._record_run_start(registry, "probe", "one", digest)


def test_run_start_refuses_changed_intent(transaction, monkeypatch):
    monkeypatch.setattr(job, "_boot_id", lambda: "394e9cfa-a8bb-4dce-8775-7a40c4368041")
    digest = _job_intent_for(transaction)
    # Intent moves after the dispatcher fenced it.
    transaction.with_suffix(".job-intent.json").write_text("{}")
    with pytest.raises(job.JobError, match="intent changed"):
        job._record_run_start(transaction.parent.parent, "probe", "one", digest)


@pytest.mark.parametrize("bad", ["", "nothex", "A" * 64, "a" * 63, "a" * 65, None, 5])
def test_run_start_rejects_invalid_digest(transaction, monkeypatch, bad):
    monkeypatch.setattr(job, "_boot_id", lambda: "394e9cfa-a8bb-4dce-8775-7a40c4368041")
    _job_intent_for(transaction)
    with pytest.raises(job.JobError, match="invalid job intent digest"):
        job._record_run_start(transaction.parent.parent, "probe", "one", bad)


@pytest.mark.parametrize(
    "boot",
    ["", "not-a-uuid", "394e9cfa8bb4dce87757a40c4368041", "394e9cfa-a8bb-4dce-8775-7a40c436804"],
)
def test_boot_id_rejects_malformed_value(monkeypatch, boot):
    """The real validator must reject anything but the canonical uuid shape."""

    class Fake:
        def read_text(self):
            return boot

    monkeypatch.setattr(job.Path, "read_text", lambda self: boot, raising=False)
    monkeypatch.setattr(job, "Path", lambda _p: Fake())
    with pytest.raises(job.JobError, match="invalid boot identity"):
        job._boot_id()


def test_boot_id_accepts_canonical_value(monkeypatch):
    monkeypatch.setattr(job, "_boot_id", lambda: "394e9cfa-a8bb-4dce-8775-7a40c4368041")
    assert job._boot_id() == "394e9cfa-a8bb-4dce-8775-7a40c4368041"


def test_run_start_missing_intent_is_refused(transaction, monkeypatch):
    monkeypatch.setattr(job, "_boot_id", lambda: "394e9cfa-a8bb-4dce-8775-7a40c4368041")
    with pytest.raises(job.JobError):
        job._record_run_start(transaction.parent.parent, "probe", "one", "a" * 64)
    assert not transaction.with_suffix(".job-run-start.json").exists()


def test_worker_records_start_before_apply(transaction, monkeypatch):
    """Ordering matters: the record must exist even if apply dies immediately."""
    recorded = []
    monkeypatch.setattr(job, "_record_run_start", lambda *a: recorded.append(a))
    monkeypatch.setattr(__import__("sys"), "argv", [
        "worker", "--registry", str(transaction.parent.parent),
        "--instance-id", "probe", "--operation-id", "one",
        "--job-intent-sha256", "a" * 64,
    ])

    from device_runtime import native_update as update

    applied = []

    class FakeUpdates:
        def __init__(self, registry):
            pass

        def apply(self, instance_id, operation_id, *, job_intent_sha256=None):
            assert recorded, "start evidence must precede apply"
            applied.append((instance_id, operation_id, job_intent_sha256))
            return {"state": "fixture"}

    monkeypatch.setattr(update, "Updates", FakeUpdates)
    job.main()
    assert applied == [("probe", "one", "a" * 64)]


# --- run-start attribution in observe_job ---

BOOT = "394e9cfa-a8bb-4dce-8775-7a40c4368041"


def _registry_with_job(transaction, *, run_start=True, apply_intent=True, boot=BOOT):
    """Build a job whose intent matches the run-start record."""
    digest = _job_intent_for(transaction)
    if not apply_intent:
        transaction.with_suffix(".apply-intent.json").unlink()
    if run_start:
        transaction.with_suffix(".job-run-start.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "state": "job_run_started",
                    "job_intent_sha256": digest,
                    "boot_id": boot,
                }
            )
        )
    return transaction.parent.parent


def _unit(stdout):
    from types import SimpleNamespace

    return lambda *a, **k: SimpleNamespace(stdout=stdout)


LOADED_FAILED = (
    "LoadState=loaded\nActiveState=failed\nSubState=failed\n"
    "Result=exit-code\nMainPID=0\nJob=\n"
)
LOADED_RUNNING = (
    "LoadState=loaded\nActiveState=activating\nSubState=start\n"
    "Result=success\nMainPID=81\nJob=700\n"
)
NOT_FOUND = (
    "LoadState=not-found\nActiveState=inactive\nSubState=dead\n"
    "Result=success\nMainPID=0\nJob=\n"
)


def test_without_run_start_keeps_existing_behaviour(transaction, monkeypatch):
    """No start evidence must not change what a failed unit already means.

    Historical transactions have no run-start record. Adding attribution must
    not retroactively downgrade states that were already decidable.
    """
    monkeypatch.setattr(job, "_boot_id", lambda: BOOT)
    registry = _registry_with_job(transaction, run_start=False)
    report = job.observe_job(
        SimpleNamespace(registry=registry), "probe", "one", runner=_unit(LOADED_FAILED)
    )
    assert report["state"] == "job_failed_requires_review"
    assert report["run_start"] is None
    assert report["retry_allowed"] is False
    assert report["fence_retained"] is True


def test_without_run_start_missing_unit_stays_unknown(transaction, monkeypatch):
    """The only thing attribution adds: a vanished unit is now explainable.

    Without start evidence this must remain the pre-existing unknown.
    """
    monkeypatch.setattr(job, "_boot_id", lambda: BOOT)
    registry = _registry_with_job(transaction, run_start=False)
    report = job.observe_job(
        SimpleNamespace(registry=registry), "probe", "one", runner=_unit(NOT_FOUND)
    )
    assert report["state"] == "dispatch_unknown"
    assert report["retry_allowed"] is False


def test_run_start_plus_failed_is_review(transaction, monkeypatch):
    monkeypatch.setattr(job, "_boot_id", lambda: BOOT)
    registry = _registry_with_job(transaction)
    report = job.observe_job(
        SimpleNamespace(registry=registry), "probe", "one", runner=_unit(LOADED_FAILED)
    )
    assert report["state"] == "job_failed_requires_review"
    assert report["retry_allowed"] is False


def test_run_start_plus_running_is_in_progress(transaction, monkeypatch):
    monkeypatch.setattr(job, "_boot_id", lambda: BOOT)
    registry = _registry_with_job(transaction)
    report = job.observe_job(
        SimpleNamespace(registry=registry), "probe", "one", runner=_unit(LOADED_RUNNING)
    )
    assert report["state"] == "job_in_progress"
    assert report["retry_allowed"] is False


def test_run_start_plus_missing_unit_reports_completion(transaction, monkeypatch):
    """A vanished unit is now attributable, and still does not permit retry."""
    monkeypatch.setattr(job, "_boot_id", lambda: BOOT)
    registry = _registry_with_job(transaction)
    report = job.observe_job(
        SimpleNamespace(registry=registry), "probe", "one", runner=_unit(NOT_FOUND)
    )
    assert report["state"] == "job_completed_no_unit_evidence"
    assert report["retry_allowed"] is False
    assert report["fence_retained"] is True




def test_run_start_for_another_dispatch_is_refused(transaction, monkeypatch):
    monkeypatch.setattr(job, "_boot_id", lambda: BOOT)
    registry = _registry_with_job(transaction)
    path = transaction.with_suffix(".job-run-start.json")
    value = json.loads(path.read_text())
    value["job_intent_sha256"] = "b" * 64
    path.write_text(json.dumps(value))
    with pytest.raises(job.JobError, match="another dispatch"):
        job.observe_job(
            SimpleNamespace(registry=registry), "probe", "one", runner=_unit(LOADED_FAILED)
        )


def test_run_start_with_extra_key_is_refused(transaction, monkeypatch):
    monkeypatch.setattr(job, "_boot_id", lambda: BOOT)
    registry = _registry_with_job(transaction)
    path = transaction.with_suffix(".job-run-start.json")
    value = json.loads(path.read_text())
    value["unexpected"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(job.JobError, match="shape differs"):
        job.observe_job(
            SimpleNamespace(registry=registry), "probe", "one", runner=_unit(LOADED_FAILED)
        )


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_run_start_special_file_is_refused(transaction, monkeypatch, kind):
    monkeypatch.setattr(job, "_boot_id", lambda: BOOT)
    _job_intent_for(transaction)
    path = transaction.with_suffix(".job-run-start.json")
    if kind == "symlink":
        target = transaction.with_suffix(".elsewhere.json")
        target.write_text("{}")
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    else:
        import os

        os.mkfifo(path)
    with pytest.raises(job.JobError):
        job.observe_job(
            SimpleNamespace(registry=transaction.parent.parent),
            "probe",
            "one",
            runner=lambda *a, **k: pytest.fail("must refuse before unit query"),
        )


def test_worker_records_start_even_when_apply_import_fails(transaction, monkeypatch):
    """A broken apply-side import must not erase the start record.

    Observed in a real container: native_update imports `packaging`, and a
    snapshot missing it aborted main() before run-start if the import came first.
    """
    recorded = []
    monkeypatch.setattr(job, "_record_run_start", lambda *a: recorded.append(a))
    import sys as real_sys

    monkeypatch.setattr(real_sys, "argv", [
        "worker", "--registry", str(transaction.parent.parent),
        "--instance-id", "probe", "--operation-id", "one",
        "--job-intent-sha256", "a" * 64,
    ])

    from device_runtime import native_update as update

    class ExplodingUpdates:
        def __init__(self, registry):
            raise ImportError("simulated missing dependency")

    monkeypatch.setattr(update, "Updates", ExplodingUpdates)
    with pytest.raises(ImportError):
        job.main()
    # Start evidence survived the failure that happened after it.
    assert recorded == [(str(transaction.parent.parent), "probe", "one", "a" * 64)]


def test_null_evidence_is_not_treated_as_absent(transaction, monkeypatch):
    """A file containing JSON null must not read as "no evidence yet".

    Absence means "not written"; null means "written and corrupt". Collapsing
    them lets a damaged apply-intent be silently skipped.
    """
    monkeypatch.setattr(job, "_boot_id", lambda: BOOT)
    _job_intent_for(transaction)
    path = transaction.with_suffix(".apply-intent.json")
    path.write_text("null")
    with pytest.raises(job.JobError, match="evidence is null"):
        job.observe_job(
            SimpleNamespace(registry=transaction.parent.parent),
            "probe",
            "one",
            runner=_unit(LOADED_FAILED),
        )


def test_missing_evidence_still_reads_as_none(transaction):
    """The absence path must keep returning None, not raise."""
    missing = transaction.with_suffix(".sender.json")
    assert not missing.exists()
    assert job._read_job_evidence(missing) is None


@pytest.mark.parametrize(
    "prepared_value,expected",
    [
        ({"foo": 1}, "lacks a metadata object"),
        ({"metadata": "string"}, "lacks a metadata object"),
        ({"metadata": None}, "lacks a metadata object"),
        ({"metadata": {"Package": "f"}}, "lacks Version"),
        ([1, 2, 3], "not an object"),
    ],
)
def test_malformed_prepared_raises_job_error(transaction, monkeypatch, prepared_value, expected):
    """Valid JSON with the wrong shape must not escape as KeyError/TypeError.

    recover_job catches JobError only; a raw KeyError would surface as a crash.
    """
    monkeypatch.setattr(job, "_boot_id", lambda: BOOT)
    _job_intent_for(transaction)
    prepared = transaction.with_suffix(".prepared.json")
    prepared.write_text(json.dumps(prepared_value))
    digest = job.helper._digest(prepared)
    intent_path = transaction.with_suffix(".job-intent.json")
    intent = json.loads(intent_path.read_text())
    intent["prepared_sha256"] = digest
    intent_path.write_text(json.dumps(intent))
    transaction.with_suffix(".approved.json").write_text(
        json.dumps({"prepared_sha256": digest, "approved_by_uid": 0})
    )
    with pytest.raises(job.JobError, match=expected):
        job.observe_job(
            SimpleNamespace(registry=transaction.parent.parent),
            "probe",
            "one",
            runner=_unit(LOADED_FAILED),
        )


def test_prepared_field_values_pass_through_unchanged(transaction, monkeypatch):
    """Shape checks must not start rejecting values the old code accepted.

    Real prepared evidence stores Version as an int in some fixtures; comparing
    it must keep working rather than raising a type error.
    """
    monkeypatch.setattr(job, "_boot_id", lambda: BOOT)
    assert job._prepared_field({"metadata": {"Version": 2}}, "Version") == 2
    assert job._prepared_field({"metadata": {"Version": "2"}}, "Version") == "2"
