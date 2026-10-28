"""Independent systemd package jobs with durable once-only dispatch intent.

Static describe_job remains a non-dispatchable unit candidate. Explicit
administrator dispatch_job uses systemd-run; it never enables boot startup.
"""

import hashlib
import json
import os
from pathlib import Path

from . import native_bootstrap as bootstrap
from . import native_helper as helper


class JobError(ValueError):
    pass


def describe_job(stem):
    """Bind an existing exactly approved apply intent to a deterministic job.

    Pure description: reads trusted transaction evidence and returns a staging
    descriptor and unit candidate. ConditionPathExists fails closed until an
    explicit future launch protocol supplies its one-shot gate. This function
    never creates that gate or writes an executable worker.
    """
    stem = helper._path(stem)
    registry = stem.parent.parent
    if stem.parent.name != "updates":
        raise JobError("canonical manager updates transaction required")
    helper._trusted(registry, directory=True)
    prepared_path = stem.with_suffix(".prepared.json")
    approved_path = stem.with_suffix(".approved.json")
    intent_path = stem.with_suffix(".apply-intent.json")
    for path in (prepared_path, approved_path, intent_path):
        helper._trusted(path)
    prepared = json.loads(prepared_path.read_bytes())
    approved = json.loads(approved_path.read_bytes())
    intent = json.loads(intent_path.read_bytes())
    digest = helper._digest(prepared_path)
    if not helper._valid_admin_approval(approved, digest):
        raise JobError("exact administrator approval required")
    snapshot = stem.with_suffix(".bootstrap")
    bootstrap.validate_snapshot(snapshot)
    expected = {
        "prepared_sha256": digest,
        "runtime_snapshot_sha256": helper._digest(snapshot / "snapshot.json"),
        "target_package": {
            "name": prepared["metadata"]["Package"],
            "version": prepared["metadata"]["Version"],
            "architecture": prepared["metadata"]["Architecture"],
        },
    }
    if not helper._same_json(intent, expected):
        raise JobError("job intent differs from approved snapshot and target")
    # This identifier has no user-controlled unit syntax or systemd expansions.
    key = hashlib.sha256(str(stem).encode()).hexdigest()
    unit = f"ai-device-package-{key}.service"
    directory = stem.with_suffix(".job")
    python = snapshot / "runtime/bin/python3.13"
    worker = directory / "worker.py"
    gate = directory / "launch-authorized"
    descriptor = {
        "schema_version": 1,
        "state": "staged_not_dispatchable",
        "transaction": str(stem),
        "intent_sha256": helper._digest(intent_path),
        "prepared_sha256": digest,
        "runtime_snapshot_sha256": expected["runtime_snapshot_sha256"],
        "unit": unit,
        "python": str(python),
        "worker": str(worker),
        "launch_gate": str(gate),
        "dispatch_allowed": False,
        "missing_requirements": ["worker_source_recheck", "lease_handoff", "one_shot_launch"],
    }
    content = (
        "[Unit]\nDescription=Device isolated package transaction (staged)\n"
        "After=local-fs.target\n"
        f"ConditionPathExists={gate}\n\n"
        "[Service]\nType=oneshot\nUser=root\nGroup=root\n"
        f"ExecStart={python} -I -B {worker}\n"
        "Restart=no\nTimeoutStartSec=infinity\nTimeoutStopSec=infinity\n"
        "KillMode=process\nSendSIGKILL=no\nUMask=0077\n"
        "StandardOutput=journal\nStandardError=journal\n"
        "# No Install section: no boot enablement or implicit retry.\n"
    )
    return {"descriptor": descriptor, "unit_content": content}


def _require_matching_worker_python_sources(snapshot_package, current_package):
    """Compare local Python source and bytecode files, not their provenance."""
    def collect(root):
        root = Path(root)
        if root.is_symlink() or not root.is_dir():
            raise JobError("snapshot worker Python source tree unavailable")
        entries = {}

        def fail(error):
            raise error

        for base, directories, files in os.walk(root, followlinks=False, onerror=fail):
            directories[:] = sorted(directories)
            for directory in directories:
                if (Path(base) / directory).is_symlink():
                    raise JobError("snapshot worker Python source tree contains symlink")
            for name in sorted(files):
                if not name.endswith((".py", ".pyc")):
                    continue
                path = Path(base) / name
                if path.is_symlink() or not path.is_file():
                    raise JobError("snapshot worker Python source tree contains invalid file")
                entries[path.relative_to(root).as_posix()] = helper._digest(path)
        if "__init__.py" not in entries:
            raise JobError("snapshot worker Python source tree unavailable")
        return entries

    try:
        if collect(snapshot_package) != collect(current_package):
            raise JobError("snapshot worker Python source tree differs")
    except OSError as exc:
        raise JobError("snapshot worker Python source tree unavailable") from exc


def _require_proven_job_fence():
    # Legacy test helpers are retired manually, not migrated. This alone does
    # not prove the live helper identity or the new protocol's runtime fence.
    # Keep dispatch closed until those clean-install guarantees are verified.
    raise JobError("new dispatch disabled: helper job-fence compatibility unproven")


def dispatch_job(manager, instance_id, operation_id, *, runner=None):
    """Explicit administrator dispatch, once only, without waiting for execution.

    The caller supplies the normal Updates manager. Never exposes arbitrary argv.
    A lost systemd response leaves a durable unknown; no automatic resubmission.
    """
    import subprocess
    import sys

    from . import native_update as update

    if sys.platform != "linux" or os.geteuid() != 0:
        raise JobError("Linux administrator required")
    if not isinstance(manager.packages, update.Debian):
        raise JobError("only Debian production adapter can dispatch")
    runner = runner or subprocess.run
    with manager._transaction(instance_id, operation_id) as (binding, stem):
        manager._prepared(binding, stem)
        path = stem.with_suffix(".job-intent.json")
        if path.exists():
            helper._trusted(path)
            intent = json.loads(path.read_bytes())
            snapshot = stem.with_suffix(".bootstrap")
            bootstrap.validate_snapshot(snapshot)
            expected = {
                "schema_version": 1,
                "state": "job_dispatch_intent",
                "close_protocol": "package-lease-v1",
                "unit": "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest()
                + ".service",
                "instance_id": instance_id,
                "operation_id": operation_id,
                "prepared_sha256": helper._digest(stem.with_suffix(".prepared.json")),
                "runtime_snapshot_sha256": helper._digest(snapshot / "snapshot.json"),
            }
            if intent != expected or type(intent.get("schema_version")) is not int:
                raise JobError("recorded job intent differs from current transaction")
            return {
                "state": "dispatch_already_recorded",
                "intent": intent,
                "replayed": False,
            }
        _require_proven_job_fence()
        with manager._apply_lease(manager.registry / "package-manager"):
            if any(stem.parent.glob("*.apply-intent.json")) or helper._pending_package_jobs(
                stem.parent
            ):
                raise JobError("existing package transaction blocks dispatch")
            snapshot = stem.with_suffix(".bootstrap")
            bootstrap.snapshot_runtime(Path(sys.base_prefix), snapshot)
            # Never launch an old seed which lacks this exact worker implementation.
            package = snapshot / "runtime/lib/python3.13/site-packages/device_runtime"
            # These checks are a necessary subset, not proof of the whole worker
            # dependency closure or authorization to dispatch.
            for module in (
                "native_update.py", "native_update_job.py", "native_helper.py", "management_lock.py"
            ):
                if helper._digest(package / module) != helper._digest(
                    Path(__file__).with_name(module)
                ):
                    raise JobError("snapshot worker code differs from dispatch implementation")
            _require_matching_worker_python_sources(package, Path(__file__).parent)
            unit = (
                "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service"
            )
            intent = {
                "schema_version": 1,
                "state": "job_dispatch_intent",
                "close_protocol": "package-lease-v1",
                "unit": unit,
                "instance_id": instance_id,
                "operation_id": operation_id,
                "prepared_sha256": helper._digest(stem.with_suffix(".prepared.json")),
                "runtime_snapshot_sha256": helper._digest(snapshot / "snapshot.json"),
            }
            helper._save_new(path, intent)
            digest = helper._digest(path)
            python = snapshot / "runtime/bin/python3.13"
            argv = [
                "/usr/bin/systemd-run",
                "--unit=" + unit,
                "--service-type=oneshot",
                "--property=Restart=no",
                "--property=RemainAfterExit=yes",
                "--property=TimeoutStartSec=infinity",
                "--property=TimeoutStopSec=infinity",
                "--property=KillMode=process",
                "--property=SendSIGKILL=no",
                "--property=UMask=0077",
                "--",
                str(python),
                "-I",
                "-B",
                "-m",
                "device_runtime.native_update_job",
                "--registry",
                str(manager.registry),
                "--instance-id",
                instance_id,
                "--operation-id",
                operation_id,
                "--job-intent-sha256",
                digest,
            ]

    # Persisted intent fences the gap after releasing leases, before dispatch.
    try:
        runner(
            argv,
            check=True,
            capture_output=True,
            timeout=30,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
        state = "dispatch_accepted"
    except (OSError, subprocess.SubprocessError):
        state = "dispatch_unknown"
    receipt = {
        "state": state,
        "intent_sha256": digest,
        "unit": unit,
        "package_execution_confirmed": False,
    }
    helper._save_new(stem.with_suffix(".job-dispatch.json"), receipt)
    return receipt


def close_before_apply(manager, instance_id, operation_id):
    """Permanently close a compatible job before apply; retain every fence."""
    import sys

    if sys.platform != "linux" or os.geteuid() != 0:
        raise JobError("Linux administrator required")
    with (
        manager._transaction(instance_id, operation_id) as (_binding, stem),
        manager._apply_lease(manager.registry / "package-manager", existing=True),
    ):
        path = stem.with_suffix(".job-intent.json")
        helper._trusted(path)
        intent = json.loads(path.read_bytes())
        unit = "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service"
        prepared = stem.with_suffix(".prepared.json")
        approved = stem.with_suffix(".approved.json")
        helper._trusted(prepared)
        helper._trusted(approved)
        digest = helper._digest(prepared)
        if (
            not isinstance(intent, dict)
            or type(intent.get("schema_version")) is not int
            or intent.get("schema_version") != 1
            or intent.get("state") != "job_dispatch_intent"
            or intent.get("close_protocol") != "package-lease-v1"
            or intent.get("instance_id") != instance_id
            or intent.get("operation_id") != operation_id
            or intent.get("unit") != unit
            or intent.get("prepared_sha256") != digest
            or not helper._valid_admin_approval(json.loads(approved.read_bytes()), digest)
        ):
            raise JobError("close intent or approval differs")
        snapshot = stem.with_suffix(".bootstrap")
        bootstrap.validate_snapshot(snapshot)
        if intent.get("runtime_snapshot_sha256") != helper._digest(snapshot / "snapshot.json"):
            raise JobError("close snapshot differs")
        if not helper._same_json(intent, {
            "schema_version": 1,
            "state": "job_dispatch_intent",
            "close_protocol": "package-lease-v1",
            "instance_id": instance_id,
            "operation_id": operation_id,
            "unit": unit,
            "prepared_sha256": digest,
            "runtime_snapshot_sha256": helper._digest(snapshot / "snapshot.json"),
        }):
            raise JobError("close intent or approval differs")
        worker = snapshot / "runtime/lib/python3.13/site-packages/device_runtime"
        for module in ("native_update.py", "native_update_job.py", "native_helper.py"):
            if helper._digest(worker / module) != helper._digest(Path(__file__).with_name(module)):
                raise JobError("close protocol worker differs")
        for suffix in (".apply-intent.json", ".sender.json", ".outcome.json"):
            if os.path.lexists(stem.with_suffix(suffix)):
                raise JobError("apply evidence exists; close refused")
        receipt = {
            "state": "closed_before_apply",
            "job_intent_sha256": helper._digest(path),
            "retry_allowed": False,
            "fence_retained": True,
        }
        marker = stem.with_suffix(".job-closed.json")
        if os.path.lexists(marker):
            helper._trusted(marker)
            if not helper._same_json(json.loads(marker.read_bytes()), receipt):
                raise JobError("close evidence differs")
            # A prior link may have succeeded while its directory fsync failed.
            # Do not report a durable close until this retry syncs the entry.
            with helper._directory_fd(marker.parent) as fd:
                os.fsync(fd)
        else:
            helper._save_new(marker, receipt)
        return receipt


def _require_proven_retirement_fence():
    """Do not publish retirement until close durability is independently proven."""
    raise JobError("job retirement disabled: close durability unproven")


def retire_closed_job(manager, instance_id, operation_id):
    """Refuse retirement until a crash-safe close protocol is available."""
    _require_proven_retirement_fence()
    closed = close_before_apply(manager, instance_id, operation_id)
    with (
        manager._transaction(instance_id, operation_id) as (_binding, stem),
        manager._apply_lease(manager.registry / "package-manager", existing=True),
    ):
        path = stem.with_suffix(".job-intent.json")
        close_path = stem.with_suffix(".job-closed.json")
        helper._trusted(path)
        helper._trusted(close_path)
        if not helper._same_json(json.loads(close_path.read_bytes()), closed) or closed[
            "job_intent_sha256"
        ] != helper._digest(path):
            raise JobError("closed evidence changed")
        if any(
            os.path.lexists(stem.with_suffix(suffix))
            for suffix in (".apply-intent.json", ".sender.json", ".outcome.json")
        ):
            raise JobError("apply evidence exists; retirement refused")
        receipt = {
            "state": "retired_before_apply",
            "job_intent_sha256": helper._digest(path),
            "closed_sha256": helper._digest(close_path),
            "old_job_replay_allowed": False,
        }
        retired = stem.with_suffix(".job-retired.json")
        if os.path.lexists(retired):
            helper._trusted(retired)
            if not helper._same_json(json.loads(retired.read_bytes()), receipt):
                raise JobError("retirement evidence changed")
        else:
            helper._save_new(retired, receipt)
        if not helper._job_retired(path):
            raise JobError("retirement evidence invalid")
        return receipt


def observe_job(manager, instance_id, operation_id, *, runner=None):
    """Read-only observation: unit status never grants retry or clears a fence."""
    import subprocess

    runner = runner or subprocess.run
    helper._identifier(instance_id)
    helper._identifier(operation_id)
    registry = helper._path(manager.registry)
    helper._trusted(registry, directory=True)
    directory = registry / "updates"
    helper._trusted(directory, directory=True)
    stem = directory / f"{len(instance_id)}-{instance_id}--{operation_id}"
    path = stem.with_suffix(".job-intent.json")
    helper._trusted(path)
    intent = json.loads(path.read_bytes())
    expected_unit = (
        "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service"
    )
    if (
        intent.get("unit") != expected_unit
        or intent.get("instance_id") != instance_id
        or intent.get("operation_id") != operation_id
    ):
        raise JobError("job observation identity differs")
    prepared_path = stem.with_suffix(".prepared.json")
    approved_path = stem.with_suffix(".approved.json")
    for evidence in (prepared_path, approved_path):
        helper._trusted(evidence)
    prepared = json.loads(prepared_path.read_bytes())
    prepared_digest = helper._digest(prepared_path)
    if intent.get("prepared_sha256") != prepared_digest or not helper._valid_admin_approval(
        json.loads(approved_path.read_bytes()), prepared_digest
    ):
        raise JobError("job approval evidence differs")
    report = {
        "state": "dispatch_unknown",
        "unit": expected_unit,
        "intent_sha256": helper._digest(path),
        "retry_allowed": False,
        "fence_retained": True,
        "unit_observation": None,
    }
    try:
        result = runner(
            [
                "/usr/bin/systemctl",
                "show",
                expected_unit,
                "--all",
                "--no-pager",
                "--property=LoadState,ActiveState,SubState,Result,MainPID,Job",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
        expected_fields = {"LoadState", "ActiveState", "SubState", "Result", "MainPID", "Job"}
        fields = {}
        valid = isinstance(result.stdout, str) and len(result.stdout) <= 16384
        if valid:
            for line in result.stdout.splitlines():
                key, separator, value = line.partition("=")
                if not separator or key not in expected_fields or key in fields:
                    valid = False
                    break
                fields[key] = value
        if valid and set(fields) == expected_fields:
            report["unit_observation"] = fields
            if fields["LoadState"] == "loaded":
                if fields["ActiveState"] in ("activating", "active", "deactivating"):
                    report["state"] = "job_in_progress"
                elif fields["ActiveState"] == "failed":
                    report["state"] = "job_failed_requires_review"
    except (OSError, subprocess.SubprocessError):
        pass
    apply_path = stem.with_suffix(".apply-intent.json")
    outcome_path = stem.with_suffix(".outcome.json")
    if os.path.lexists(apply_path):
        helper._trusted(apply_path)
        apply_intent = json.loads(apply_path.read_bytes())
        expected_apply = {
            "prepared_sha256": prepared_digest,
            "runtime_snapshot_sha256": intent.get("runtime_snapshot_sha256"),
            "target_package": {
                "name": prepared["metadata"]["Package"],
                "version": prepared["metadata"]["Version"],
                "architecture": prepared["metadata"]["Architecture"],
            },
        }
        if not helper._same_json(apply_intent, expected_apply):
            raise JobError("job apply evidence differs")
        report["apply_intent_present"] = True
        if outcome_path.exists():
            helper._trusted(outcome_path)
            outcome = json.loads(outcome_path.read_bytes())
            sender_path = stem.with_suffix(".sender.json")
            helper._trusted(sender_path)
            sender = json.loads(sender_path.read_bytes())
            if (
                not helper._same_json(outcome.get("intent"), apply_intent)
                or not helper._same_json(sender.get("intent"), apply_intent)
                or sender.get("state") != "sender_reaped"
                or type(sender.get("returncode")) is not int
                or outcome.get("state") not in ("unknown", "package_applied_pending_activation")
            ):
                raise JobError("job outcome evidence differs")
            expected_package = {
                "status": "install ok installed",
                "version": prepared["metadata"]["Version"],
                "architecture": prepared["metadata"]["Architecture"],
            }
            if outcome["state"] == "package_applied_pending_activation" and (
                sender["returncode"] != 0
                or not helper._same_json(outcome.get("package_observation"), expected_package)
            ):
                raise JobError("job success evidence differs")
            report["state"] = "package_outcome_recorded"
            report["outcome"] = outcome
    else:
        if any(os.path.lexists(stem.with_suffix(suffix)) for suffix in (".sender.json", ".outcome.json")):
            raise JobError("job execution evidence without apply intent")
        report["apply_intent_present"] = False
    return report


def recover_job(manager, instance_id, operation_id, *, runner=None):
    """Explicit evidence reconciliation only, never retry or release a fence."""
    import sys

    from . import native_update as update

    if sys.platform != "linux" or os.geteuid() != 0:
        raise JobError("Linux administrator required")
    update._require_proven_job_recovery_fence()
    observation = observe_job(manager, instance_id, operation_id, runner=runner)
    unit = observation["unit_observation"] or {}
    if (
        unit.get("LoadState") != "loaded"
        or not (
            unit.get("ActiveState") in ("inactive", "failed")
            or (
                unit.get("ActiveState") == "active"
                and unit.get("SubState") == "exited"
                and unit.get("Result") == "success"
            )
        )
        or unit.get("MainPID") != "0"
        or unit.get("Job") not in ("", "0")
        or not observation["apply_intent_present"]
    ):
        raise JobError("completed job and apply evidence required; remains blocked")
    # recover rechecks approval/source/sender and acquires the global EX lease.
    result = manager.recover(
        instance_id, operation_id, job_intent_sha256=observation["intent_sha256"]
    )
    with manager._transaction(instance_id, operation_id) as (_binding, stem):
        if helper._digest(stem.with_suffix(".job-intent.json")) != observation["intent_sha256"]:
            raise JobError("job recovery intent changed")
        receipt = {
            "state": "job_reconciled_without_replay",
            "job_intent_sha256": observation["intent_sha256"],
            "recovery_sha256": helper._digest(stem.with_suffix(".recovery.json")),
            "recovery_state": result["recovery"]["state"],
            "retry_allowed": False,
            "fence_retained": True,
        }
        path = stem.with_suffix(".job-recovery.json")
        if path.exists():
            helper._trusted(path)
            if not helper._same_json(json.loads(path.read_bytes()), receipt):
                raise JobError("job recovery evidence changed")
        else:
            helper._save_new(path, receipt)
        return receipt


def main():
    import argparse

    from .native_update import Updates

    parser = argparse.ArgumentParser(description="Fixed independent package transaction worker")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--job-intent-sha256", required=True)
    args = parser.parse_args()
    result = Updates(args.registry).apply(
        args.instance_id, args.operation_id, job_intent_sha256=args.job_intent_sha256
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
