"""Staged systemd package-job descriptions, deliberately not dispatchable yet.

The manager/lease handoff and worker-side source checks are not implemented.
No unit is written into systemd directories, enabled or started by this module.
"""

import hashlib
import json

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
