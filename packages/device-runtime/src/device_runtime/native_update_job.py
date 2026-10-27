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
    if approved != {"prepared_sha256": digest, "approved_by_uid": 0}:
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
    if intent != expected:
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
            return {
                "state": "dispatch_already_recorded",
                "intent": json.loads(path.read_bytes()),
                "replayed": False,
            }
        with manager._apply_lease(manager.registry / "package-manager"):
            if any(stem.parent.glob("*.apply-intent.json")) or any(
                stem.parent.glob("*.job-intent.json")
            ):
                raise JobError("existing package transaction blocks dispatch")
            snapshot = stem.with_suffix(".bootstrap")
            bootstrap.snapshot_runtime(Path(sys.base_prefix), snapshot)
            # Never launch an old seed which lacks this exact worker implementation.
            package = snapshot / "runtime/lib/python3.13/site-packages/device_runtime"
            for module in ("native_update.py", "native_update_job.py", "native_helper.py"):
                if helper._digest(package / module) != helper._digest(
                    Path(__file__).with_name(module)
                ):
                    raise JobError("snapshot worker code differs from dispatch implementation")
            unit = (
                "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service"
            )
            intent = {
                "schema_version": 1,
                "state": "job_dispatch_intent",
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
