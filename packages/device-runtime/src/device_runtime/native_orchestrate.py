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
import re
import subprocess
import sys
from pathlib import Path

from . import native_helper as helper
from . import native_prepare as prepare
from . import native_update as update

# Exit status used when a child refuses or fails; the parent propagates it.
FAILED = 1

# The bootstrap pointer names a directory, never a path.
TREE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class OrchestrationError(ValueError):
    """Raised when a step of the sequence refuses to continue."""


def _require_root(step):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise OrchestrationError(f"{step} requires administrator privileges")


def _instance_owner(registry, instance_id):
    """Resolve the registered non-root owner of ``instance_id``."""
    record = helper.Helper(registry)._binding(instance_id)
    uid, gid = record.get("uid"), record.get("gid")
    if type(uid) is not int or type(gid) is not int or uid <= 0 or gid <= 0:
        raise OrchestrationError("registered instance has no non-root owner")
    return uid, gid, Path(record["root"])


def _runtime_receipt(root, operation_id):
    """Name the receipt ``prepare_runtime_candidate`` writes for this operation.

    The stem is that function's own coordinate inside the instance tree; the
    signed preparation path revalidates the file's parent, owner and contents
    against the authenticated release, so naming it here does not vouch for it.
    """
    return Path(root) / "native-candidates" / f"{operation_id}.result.json"


def _transaction_stem(registry, instance_id, operation_id):
    """Name the update transaction the same way ``Updates`` does.

    IDs permit ``--``, so the first component is length-prefixed: without it two
    distinct (instance, operation) pairs could alias one transaction directory.
    """
    return (
        Path(registry)
        / "updates"
        / f"{len(instance_id)}-{instance_id}--{operation_id}"
    )


def _uv_executable(explicit):
    """Resolve the persistent uv the preparation step requires.

    ``prepare_runtime_candidate`` treats uv as an administrator-owned tool, not
    an optional hint, so the packaged copy next to this runtime is the default
    rather than an omitted argument that would fail later as a TypeError.
    """
    if explicit is not None:
        return explicit
    packaged = Path(sys.base_prefix).parent / "tools" / "uv"
    if not packaged.is_file():
        raise OrchestrationError(
            "--uv-executable required: no packaged uv beside this runtime"
        )
    return str(packaged)


def _bootstrap_tree(name):
    """Validate the tree the bootstrap pointer must end up naming.

    This is a directory component, not the release version: the pointer holds
    the versioned bootstrap directory name, while the version string is only
    compared against ``bootstrap_min`` during signature verification.

    A same-name reinstall is legitimate, so the tree running this command is not
    rejected here. ``switch_helper`` still verifies that the named directory is
    an installed, runnable bootstrap before the pointer moves.
    """
    if not isinstance(name, str) or not TREE_NAME.fullmatch(name):
        raise OrchestrationError("invalid bootstrap tree name")
    return name


def _drop_identity(uid, gid):
    """Become ``uid``/``gid`` and reset the home-derived state that follows it.

    The child inherits this process's environment, which belongs to root: with
    ``HOME=/root`` still set, the packaged uv cannot create its cache after the
    drop and the owner step fails on a permission error that looks like nothing
    to do with identity. The home directory is read from the account database
    here rather than taken from the environment, and the per-user cache and
    config roots move with it for the tools that consult them.
    """
    import pwd

    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    if os.geteuid() != uid or os.geteuid() == 0:
        raise OrchestrationError("privilege drop failed")
    try:
        home = pwd.getpwuid(uid).pw_dir
    except KeyError as exc:
        raise OrchestrationError("owner account has no home directory") from exc
    if not home:
        raise OrchestrationError("owner account has no home directory")
    os.environ["HOME"] = home
    os.environ["USER"] = pwd.getpwuid(uid).pw_name
    os.environ["LOGNAME"] = os.environ["USER"]
    os.environ["XDG_CACHE_HOME"] = home + "/.cache"
    os.environ["XDG_CONFIG_HOME"] = home + "/.config"


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
            _drop_identity(uid, gid)
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
    bootstrap_tree=None,
    trusted_keys=None,
    uv_executable=None,
    stopped=False,
):
    """Run the upgrade sequence for one already-stopped instance.

    ``stopped`` must come from the caller after actually stopping the instance;
    this never stops or starts a service itself.

    ``bootstrap_version`` is only the version compared against the signed
    ``bootstrap_min``; ``bootstrap_tree`` is the versioned directory the pointer
    must name afterwards. Defaulting it to the version string silently produced
    a pointer to a tree that was never installed.
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
    tree = _bootstrap_tree(
        bootstrap_tree if bootstrap_tree is not None else bootstrap_version
    )
    uv = _uv_executable(uv_executable)

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
            uv_executable=uv,
        ),
    )

    updates = update.Updates(registry)
    release = prepared["candidate"]["release"]

    # Step 2 (root): re-verify the package against the authenticated envelope
    # and bind the signed approval to the exact prepared artifact. This step
    # reads the owner's receipt file, not the parsed value, and revalidates its
    # parent, owner and contents against the authenticated release.
    keys = prepare._load_trusted_keys(trusted_keys)
    # The return value is the recorded intent, used only as evidence of success;
    # the approval below names the digest of the prepared file that intent
    # describes, so nothing here reads a field the caller might not provide.
    updates.prepare_signed(
        instance_id,
        operation_id,
        package,
        envelope_path=envelope,
        trusted_keys=keys,
        release_id=release_id,
        runtime_receipt=_runtime_receipt(root, operation_id),
        bootstrap_version=bootstrap_version,
    )
    # ``prepare_signed`` does not return a digest of the file it wrote, so the
    # approval names the digest of that exact file. ``approve`` recomputes it
    # under the transaction lease and refuses a mismatch, which is what makes
    # this reading authoritative instead of assumed.
    prepared_sha256 = helper._digest(
        _transaction_stem(registry, instance_id, operation_id).with_suffix(".prepared.json")
    )

    # Step 3 (root): record the administrator approval for that digest.
    updates.approve(instance_id, operation_id, prepared_sha256=prepared_sha256)

    # Step 4 (root): apply the package. The instance is already stopped.
    applied = updates.apply(instance_id, operation_id)
    # A returned observation is not a success claim: apply reports a refused or
    # unproven replacement as ``unknown`` instead of raising, and continuing from
    # there would switch a pointer onto a package that was never installed.
    applied_state = (applied or {}).get("applied") or {}
    if not isinstance(applied_state, dict) or applied_state.get("state") != "applied_package":
        raise OrchestrationError(
            "package apply did not reach applied_package; sequence stopped before activation"
        )

    # Step 5 (root): move the bootstrap pointer to the installed tree.
    switched = updates.switch_helper(
        instance_id, operation_id, expected_version=tree
    )
    switched_record = (switched or {}).get("helper_switched") or {}
    if not isinstance(switched_record, dict) or switched_record.get("state") != "helper_switched":
        raise OrchestrationError(
            "bootstrap switch recorded no helper_switched receipt; sequence stopped before activation"
        )

    # Step 6 (owner): promote the prepared release and rewrite active.json.
    # The activation step takes the key file path and re-loads it there, so it
    # repeats the trust check inside the owner's process instead of inheriting
    # this one's parsed result.
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
    if not isinstance(activated, dict) or activated.get("state") != "activated":
        raise OrchestrationError("candidate activation returned no activated state")

    return {
        "instance_id": instance_id,
        "operation_id": operation_id,
        "release": release,
        "prepared_sha256": prepared_sha256,
        "bootstrap_tree": tree,
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
    parser.add_argument(
        "--bootstrap-version",
        required=True,
        help="version compared against the signed bootstrap_min",
    )
    parser.add_argument(
        "--bootstrap-tree",
        default=None,
        help=(
            "versioned bootstrap directory the pointer must name after the switch; "
            "defaults to --bootstrap-version"
        ),
    )
    parser.add_argument("--trusted-keys", default=None)
    parser.add_argument(
        "--uv-executable",
        default=None,
        help="persistent uv; defaults to the uv shipped beside this runtime",
    )
    parser.add_argument(
        "--stopped",
        action="store_true",
        help="confirm the instance is already stopped; this never stops it",
    )
    args = vars(parser.parse_args())
    try:
        print(json.dumps(upgrade(**args), sort_keys=True))
    except (OrchestrationError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        # ``helper``/``update`` refusals derive from ValueError, so this reports
        # them as a refusal rather than leaking a traceback from the CLI.
        parser.exit(1, f"upgrade refused: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
