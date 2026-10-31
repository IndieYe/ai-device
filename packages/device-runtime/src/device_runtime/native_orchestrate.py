"""One-shot orchestration for install and upgrade.

The individual steps keep their own privilege checks and refusal conditions;
this module only sequences them across the two identities they require.

``prepare`` and ``activate`` must run as the instance owner (non-root, root
owned by that account), while signed verification, approval, package
application and the bootstrap switch must run as root. Rather than loosening
any of those checks, the orchestration forks a child, drops it to the instance
account for the owner-only steps, and keeps the privileged steps in the root
parent.

Failure stops the sequence. No step is retried and no completed step is undone:
each step already writes its own durable evidence, so a rerun with the same
operation id observes that evidence instead of replaying it.
"""

import json
import os
import sys
from pathlib import Path

from . import native_helper as helper
from . import native_prepare as prepare
from . import native_update as update

# Exit status used when a child refuses or fails; the parent propagates it.
FAILED = 1


class OrchestrationError(ValueError):
    """Raised when a step of the sequence refuses to continue."""


def _require_root(step):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise OrchestrationError(f"{step} requires administrator privileges")


def _instance_owner(registry, instance_id):
    """Resolve the registered non-root owner of ``instance_id``."""
    record = helper.Helper(registry).binding(instance_id)
    uid, gid = record.get("uid"), record.get("gid")
    if type(uid) is not int or type(gid) is not int or uid == 0:
        raise OrchestrationError("registered instance has no non-root owner")
    return uid, gid, Path(record["root"])


def _fork_as(uid, gid, work):
    """Run ``work`` in a child dropped to ``uid``/``gid`` and return its result.

    Only the child changes identity; the parent stays root so the following
    privileged steps keep working. The result comes back as JSON over a pipe,
    so a child that refuses leaves the parent with nothing to misread.
    """
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # child
        try:
            os.close(read_fd)
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            if os.geteuid() != uid or os.geteuid() == 0:
                raise OrchestrationError("privilege drop failed")
            payload = json.dumps(work(), sort_keys=True).encode()
            with os.fdopen(write_fd, "wb") as stream:
                stream.write(payload)
            os._exit(0)
        except BaseException as exc:  # noqa: BLE001 - report, never leak a traceback
            try:
                with os.fdopen(write_fd, "wb") as stream:
                    stream.write(
                        json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode()
                    )
            except BaseException:  # noqa: BLE001, S110 - already failing; parent reports the exit
                pass
            os._exit(FAILED)

    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as stream:
        raw = stream.read()
    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)

    if code != 0:
        detail = ""
        if raw:
            try:
                detail = json.loads(raw).get("error", "")
            except ValueError:
                detail = raw.decode("utf-8", "replace")
        raise OrchestrationError(f"owner step failed: {detail or f'exit {code}'}")
    if not raw:
        raise OrchestrationError("owner step returned no result")
    return json.loads(raw)


def upgrade(
    *,
    registry,
    instance_id,
    operation_id,
    package,
    envelope,
    release_id,
    bootstrap_version,
    trusted_keys=None,
    uv_executable=None,
    stopped=False,
):
    """Run the upgrade sequence for one already-stopped instance.

    ``stopped`` must come from the caller after actually stopping the instance;
    this never stops or starts a service itself.
    """
    _require_root("upgrade")
    if not stopped:
        raise OrchestrationError(
            "explicit --stopped confirmation required; stop the instance first"
        )

    registry = helper._path(registry)
    uid, gid, root = _instance_owner(registry, instance_id)
    package = helper._path(package)
    envelope = helper._path(envelope)

    # Step 1 (owner): authenticate the envelope and prepare the runtime
    # candidate in the final release directory.
    prepared = _fork_as(
        uid,
        gid,
        lambda: prepare.prepare_runtime_candidate(
            root,
            package,
            envelope,
            trusted_keys=prepare._load_trusted_keys(trusted_keys),
            release_id=release_id,
            operation_id=operation_id,
            bootstrap_version=bootstrap_version,
            uv_executable=uv_executable,
        ),
    )

    updates = update.Updates(registry)
    release = prepared["candidate"]["release"]

    # Step 2 (root): re-verify the package against the authenticated envelope
    # and bind the signed approval to the exact prepared artifact. This step
    # takes the parsed key set, not a path, which is how the CLI hands it over.
    keys = prepare._load_trusted_keys(trusted_keys)
    signed = updates.prepare_signed(
        instance_id,
        operation_id,
        package,
        envelope_path=envelope,
        trusted_keys=keys,
        release_id=release_id,
        runtime_receipt=prepared,
        bootstrap_version=bootstrap_version,
    )

    # Step 3 (root): record the administrator approval for that digest.
    updates.approve(
        instance_id, operation_id, prepared_sha256=signed["prepared_sha256"]
    )

    # Step 4 (root): apply the package. The instance is already stopped.
    applied = updates.apply(instance_id, operation_id)

    # Step 5 (root): move the bootstrap pointer to the installed tree. The
    # expected value is the bootstrap tree name, not the release UUID.
    switched = updates.switch_helper(
        instance_id, operation_id, expected_version=bootstrap_version
    )

    # Step 6 (owner): promote the prepared release and rewrite active.json.
    activated = _fork_as(
        uid,
        gid,
        lambda: prepare.activate_candidate(
            root,
            release,
            envelope=envelope,
            trusted_keys=trusted_keys,
            stopped=stopped,
        ),
    )

    return {
        "instance_id": instance_id,
        "operation_id": operation_id,
        "release": release,
        "prepared_sha256": signed["prepared_sha256"],
        "applied": applied,
        "switched": switched,
        "activated": activated,
        "service_started": False,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the full upgrade sequence for one stopped native instance"
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--envelope", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--bootstrap-version", required=True)
    parser.add_argument("--trusted-keys", default=None)
    parser.add_argument("--uv-executable", default=None)
    parser.add_argument(
        "--stopped",
        action="store_true",
        help="confirm the instance is already stopped; this never stops it",
    )
    args = vars(parser.parse_args())
    try:
        print(json.dumps(upgrade(**args), sort_keys=True))
    except (OrchestrationError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"upgrade refused: {exc}\n")


if __name__ == "__main__":
    main()
