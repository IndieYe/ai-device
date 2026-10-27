import json

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
    real_digest = job.helper._digest

    def digest(path):
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py"):
            return "same-worker-bytes"
        return real_digest(path)

    monkeypatch.setattr(job.helper, "_digest", digest)

    held = []

    @contextmanager
    def lease(*a):
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    @contextmanager
    def txn(*a):
        yield {}, transaction

    manager = SimpleNamespace(
        packages=update.Debian(),
        registry=transaction.parent.parent,
        _transaction=txn,
        _prepared=lambda *a: {},
        _apply_lease=lease,
    )
    calls = []

    def runner(argv, **kw):
        assert not held, "release package lease before independent worker starts"
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
    assert "--property=Restart=no" in calls[0]
    assert "--property=RemainAfterExit=yes" in calls[0]


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
    original_digest = job.helper._digest

    def digest(path):
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py"):
            return "same-worker-bytes"
        return original_digest(path)

    monkeypatch.setattr(job.helper, "_digest", digest)

    @contextmanager
    def txn(*args):
        yield {}, transaction

    @contextmanager
    def lease(*args):
        yield

    manager = SimpleNamespace(
        packages=update.Debian(), registry=transaction.parent.parent,
        _transaction=txn, _prepared=lambda *args: {}, _apply_lease=lease,
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
    original_digest = job.helper._digest
    original_save = job.helper._save_new
    parent_pid = os.getpid()
    runner_marker = transaction.parent / "unexpected-runner"

    def digest(path):
        if path.name in ("native_update.py", "native_update_job.py", "native_helper.py"):
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
    def txn(*args):
        yield {}, transaction

    @contextmanager
    def lease(*args):
        yield

    monkeypatch.setattr(job.helper, "_digest", digest)
    monkeypatch.setattr(job.helper, "_save_new", save_and_exit)
    manager = SimpleNamespace(
        packages=update.Debian(), registry=transaction.parent.parent,
        _transaction=txn, _prepared=lambda *args: {}, _apply_lease=lease,
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
    def txn(*args):
        yield {}, transaction

    manager = SimpleNamespace(packages=update.Debian(), _transaction=txn, _prepared=lambda *a: {})
    calls = []
    with pytest.raises(job.JobError, match="recorded job intent differs"):
        job.dispatch_job(manager, "probe", "one", runner=lambda *a, **kw: calls.append(a))
    assert path.read_bytes() == before and not calls


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
    def txn(*args):
        yield {}, transaction

    @contextmanager
    def lease(*args):
        with job.helper.package_lease(registry, exclusive=True) as fd:
            yield fd

    manager = SimpleNamespace(
        packages=update.Debian(), registry=registry, _transaction=txn,
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
        return SimpleNamespace(stdout=f"LoadState=loaded\nActiveState={unit_state}\n")

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
    with pytest.raises(FileNotFoundError):
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
    def txn(*args):
        yield {}, transaction

    manager = SimpleNamespace(
        packages=update.Debian(),
        registry=transaction.parent.parent,
        _transaction=txn,
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
    def txn(*args):
        yield {}, transaction

    manager = SimpleNamespace(
        packages=update.Debian(),
        registry=transaction.parent.parent,
        _transaction=txn,
        _prepared=lambda *args: {},
    )
    result = job.dispatch_job(
        manager, "probe", "one", runner=lambda *a, **k: pytest.fail("replayed")
    )
    assert result["state"] == "dispatch_already_recorded" and not result["replayed"]
