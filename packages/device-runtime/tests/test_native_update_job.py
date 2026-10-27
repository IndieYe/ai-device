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

    first = job.dispatch_job(manager, "probe", "one", runner=runner)
    assert first["state"] == ("dispatch_unknown" if lost else "dispatch_accepted")
    assert first["package_execution_confirmed"] is False
    second = job.dispatch_job(manager, "probe", "one", runner=runner)
    assert second["state"] == "dispatch_already_recorded" and not second["replayed"]
    assert len(calls) == 1
    assert calls[0][0] == "/usr/bin/systemd-run"
    assert "--property=Restart=no" in calls[0]
