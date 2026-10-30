"""Independent single-instance upgrade preparation and crash-safe publication.

No service is stopped here. Service orchestration must prove task drain separately.
"""

import fcntl
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import installation as install
from . import lifecycle as life
from .bundle import inspect_bundle
from .management_lock import legacy_operation


class UpgradeError(ValueError):
    """Controlled upgrade failure; callers must not replay unknown transitions."""


def _save(path, value):
    # active is a non-secret boot pointer read by a different client identity.
    # Preserve the explicitly provisioned ownership/read contract on replacement;
    # operation records remain private. Never grant group/other write access.
    pointer_info = path.lstat() if path.name == "active.json" else None
    if pointer_info is not None and (
        not stat.S_ISREG(pointer_info.st_mode) or pointer_info.st_mode & 0o7022
    ):
        raise UpgradeError("active pointer has unsafe permissions")
    fd, temporary = tempfile.mkstemp(prefix=".upgrade-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            if pointer_info is not None:
                current = os.fstat(stream.fileno())
                if (current.st_uid, current.st_gid) != (pointer_info.st_uid, pointer_info.st_gid):
                    os.fchown(stream.fileno(), pointer_info.st_uid, pointer_info.st_gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(pointer_info.st_mode))
            else:
                os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _operation_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise UpgradeError("invalid upgrade ID")
    return value


@contextmanager
def _supervision_lease(root, *, exclusive):
    path = root / "upgrades/.supervisor.lock"
    if path.is_symlink():
        raise UpgradeError("invalid supervisor lock")
    with path.open("a+b") as lease:
        try:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(lease, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpgradeError("supervised maintenance already running") from None
        yield


def _policy(root, *, release=None, fingerprint=None):
    path = root / "upgrade-profile.json"
    if path.exists() or path.is_symlink():
        from .upgrade_supervisor import profile

        settings = profile(root)
        if release is not None:
            from .upgrade_policy import check_release

            check_release(settings, release=release, fingerprint=fingerprint)


@contextmanager
def _control(root, upgrade_id, *, supervised=False, strict_pointer=True):
    root = life._root(root)
    life._layout(root, online=True, strict_pointer=strict_pointer)
    _operation_id(upgrade_id)
    directory = root / "upgrades"
    if directory.is_symlink():
        raise UpgradeError("invalid upgrade directory")
    directory.mkdir(exist_ok=True, mode=0o700)
    # Serialize all upgrade operations, not just identical IDs.
    lock_path = directory / ".lock"
    if lock_path.is_symlink():
        raise UpgradeError("invalid upgrade lock")
    from contextlib import nullcontext

    coordination = nullcontext() if supervised else _supervision_lease(root, exclusive=False)
    with coordination, lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpgradeError("another upgrade is running") from exc
        path = directory / (upgrade_id + ".json")
        if path.is_symlink():
            raise UpgradeError("invalid upgrade record")
        yield root, path


def _baseline(root):
    # Recovery must be able to describe an interrupted publication, where the
    # pointer and active.json legitimately disagree; repair happens later.
    active, identity = life._layout(root, online=True, strict_pointer=False)
    return life._binding(root, active, identity)


def _pending(root, own):
    for path in (root / "upgrades").glob("*.json"):
        if path == own:
            continue
        record = life._read(path)
        if record["state"] not in {"verified_local", "succeeded", "failed", "cancelled"}:
            raise UpgradeError("another nonterminal upgrade requires reconciliation")


def _active_runtime(instance, release):
    """Return the active release's private runtime, or None for legacy instances."""
    if not install._has_pointer(instance):
        return None
    runtime = instance / "releases" / release / "runtime"
    if not runtime.is_dir() or runtime.is_symlink():
        raise UpgradeError("active release has no private runtime")
    return runtime


@legacy_operation
def prepare(root, bundle, upgrade_id):
    """Online candidate build; never mutate active/config/state or take runtime lease."""
    bundle = Path(bundle).resolve()
    manifest = inspect_bundle(bundle)
    life._compatible(manifest)
    request = {"bundle_sha256": install._fingerprint(manifest)}
    with _control(root, upgrade_id) as (instance, path):
        _policy(instance, release=manifest["release_version"], fingerprint=request["bundle_sha256"])
        if path.exists():
            previous = life._read(path)
            if previous["request"] != request:
                raise UpgradeError("upgrade ID request conflict")
            return previous
        with (instance / ".installation.lock").open("rb") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise UpgradeError("instance maintenance is running") from exc
            _pending(instance, path)
            baseline = _baseline(instance)
            record = {
                "format_version": 1,
                "upgrade_id": upgrade_id,
                "request": request,
                "baseline": baseline,
                "state": "preparing",
                "candidate": None,
            }
            _save(path, record)
            try:
                candidate = install._prepare(
                    instance,
                    bundle,
                    manifest,
                    baseline["active"]["roles"],
                    runtime_source=_active_runtime(instance, baseline["active"]["release"]),
                )
                record["candidate"] = candidate
                if _baseline(instance) != baseline:
                    raise UpgradeError("instance changed during preparation")
                record["state"] = "prepared"
                _save(path, record)
            except (OSError, ValueError, KeyError, TypeError):
                # Candidate may exist. It remains unbound and is never auto-deleted/retried.
                record["state"] = "failed"
                record["code"] = "candidate_preparation_failed"
                _save(path, record)
                raise UpgradeError(
                    "candidate preparation failed; active release unchanged"
                ) from None
        return record


def status(root, upgrade_id):
    """Read only, including when interrupted; does not manufacture missing records."""
    root = life._root(root)
    path = root / "upgrades" / (_operation_id(upgrade_id) + ".json")
    if path.is_symlink():
        raise UpgradeError("invalid upgrade record")
    return life._read(path)


def _publication_pointer(record):
    """Validate a durable intent without regenerating its activation identity."""
    pointer = record.get("expected_active")
    if not isinstance(pointer, dict):
        raise UpgradeError("invalid expected active pointer")
    activation = pointer.get("activation_id")
    if not isinstance(activation, str) or not re.fullmatch(r"[0-9a-f]{32}", activation):
        raise UpgradeError("invalid expected active pointer")
    expected = {**record["candidate"], "activation_id": activation}
    original = record["baseline"]["active"]
    if "task_output_map_sha256" in original:
        expected["task_output_map_sha256"] = original["task_output_map_sha256"]
    if pointer != expected:
        raise UpgradeError("invalid expected active pointer")
    return pointer


@legacy_operation
def activate(root, upgrade_id, *, stopped=False, _supervised=False, _recovery=False):
    """Publish once after an external supervisor has stopped/drained the instance.

    Publication success is NOT service health or central reauthentication success.
    """
    if not stopped:
        raise UpgradeError("explicit stopped confirmation required")
    with _control(root, upgrade_id, supervised=_supervised, strict_pointer=False) as (
        instance,
        path,
    ):
        record = life._read(path)
        recovering = (
            _recovery
            and _supervised
            and record["state"] == "unknown"
            and record.get("supervisor", {}).get("phase") == "recovering-publish"
        )
        if not recovering and record["state"] not in {
            "prepared",
            "published",
            "verified_local",
            "succeeded",
        }:
            raise UpgradeError("upgrade requires reconciliation, not replay")
        _policy(
            instance,
            release=record["candidate"]["release_version"],
            fingerprint=record["request"]["bundle_sha256"],
        )
        if record["state"] in {"published", "verified_local", "succeeded"}:
            return record
        with install._lock(instance):
            if life._read(path) != record:
                raise UpgradeError("upgrade record changed; inspect again")
            if _baseline(instance) != record["baseline"]:
                raise UpgradeError("stale candidate; current instance differs")
            candidate = record["candidate"]
            checked = life._check_release(instance, candidate["release"], candidate["roles"])
            if checked != candidate:
                raise UpgradeError("candidate changed")
            # Save exact expected pointer before publication, enabling interrupted reconciliation.
            import uuid

            if recovering and "expected_active" in record:
                pointer = _publication_pointer(record)
            else:
                pointer = {**candidate, "activation_id": uuid.uuid4().hex}
                current = life._read(instance / "active.json")
                if "task_output_map_sha256" in current:
                    pointer["task_output_map_sha256"] = current["task_output_map_sha256"]
            record.update(state="publishing", expected_active=pointer)
            _save(path, record)
            # The launcher starts whatever the pointer names; move it before the
            # metadata publication so a crash cannot leave them disagreeing.
            if install._has_pointer(instance):
                install._write_pointer(instance, pointer["release"])
            _save(instance / "active.json", pointer)
            record["state"] = "published"
            _save(path, record)
        return record


@legacy_operation
def reconcile(root, upgrade_id):
    """Reconcile pointer publication only; never restart services or replay a switch."""
    with _control(root, upgrade_id, strict_pointer=False) as (instance, path):
        record = life._read(path)
        if record["state"] == "publishing":
            with install._lock(instance):
                current = life._read(instance / "active.json")
                if current == record["expected_active"]:
                    record["state"] = "published"
                else:
                    record["state"] = "unknown"
                _save(path, record)
        return record


def add_parser(modes):
    parser = modes.add_parser(
        "upgrade", help="Local upgrades; supervise has service stop/start side effects"
    )
    commands = parser.add_subparsers(dest="upgrade_command", required=True)
    for name in (
        "prepare",
        "status",
        "activate",
        "reconcile",
        "supervise",
        "resume-admission",
        "reconcile-supervision",
        "recover",
    ):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--upgrade-id", required=True)
        if name == "prepare":
            command.add_argument("--bundle", type=Path, required=True)
        if name == "recover":
            command.add_argument(
                "--action", choices=("publish", "start-original", "start-candidate"), required=True
            )
            command.add_argument("--expected-record-sha256", required=True)
        if name in {"supervise", "resume-admission", "recover"}:
            command.add_argument("--confirm-service-change", action="store_true")
        if name == "activate":
            command.add_argument("--stopped", action="store_true")


def run(args):
    if args.upgrade_command == "recover":
        from .upgrade_supervisor import recover

        result = recover(
            args.root,
            args.upgrade_id,
            action=args.action,
            expected_record_sha256=args.expected_record_sha256,
            confirmed=args.confirm_service_change,
        )
    elif args.upgrade_command == "reconcile-supervision":
        from .upgrade_supervisor import reconcile_supervision

        result = reconcile_supervision(args.root, args.upgrade_id)
    elif args.upgrade_command == "resume-admission":
        from .upgrade_supervisor import resume_admission

        result = resume_admission(args.root, args.upgrade_id, confirmed=args.confirm_service_change)
    elif args.upgrade_command == "supervise":
        from .upgrade_supervisor import supervise

        result = supervise(args.root, args.upgrade_id, confirmed=args.confirm_service_change)
    elif args.upgrade_command == "prepare":
        result = prepare(args.root, args.bundle, args.upgrade_id)
    elif args.upgrade_command == "activate":
        result = activate(args.root, args.upgrade_id, stopped=args.stopped)
    elif args.upgrade_command == "reconcile":
        result = reconcile(args.root, args.upgrade_id)
    else:
        result = status(args.root, args.upgrade_id)
    # Internal baseline can contain private configuration; expose only bounded status.
    print(
        json.dumps(
            {
                "upgrade_id": result["upgrade_id"],
                "state": result["state"],
                "release": (result.get("candidate") or {}).get("release"),
            }
        )
    )
