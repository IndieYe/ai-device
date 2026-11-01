"""Linux native service helper: administrator provisioning and registry checks.

This module no longer runs a daemon. It provides the root-only provisioning
commands (``register``, ``recover-registration``, ``unregister``) plus the
helpers that the upgrade path reuses for binding validation, leases and
systemd observation. There is no socket, IPC or request dispatch: upgrade work
is driven by explicit one-shot root commands instead.

No stop, arbitrary argv, installation, package activation or automatic replay.
"""

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .entrypoint import LAUNCHER, POINTER, RUNNER_BODY
from .management_lock import MARKER, management_lease

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
# The runtime directory that used to host the helper socket. Kept as a named
# constant because it is still the documented location for runtime state; the
# daemon that consumed it is retired.
RUNTIME_ROOT = Path("/run")
# Registry of registered instances and package-manager locks. It must not be
# /var/lib/ai-device/manager: that path is the legacy marker the package
# pre-install guards treat as "a controlled transaction is required", so a
# helper deployed onto it would make every later upgrade refuse itself.
# Registration records already live here, and the uninstall guards check it.
REGISTRY = Path("/var/lib/ai-device/native")
# Paths the package pre-install guards treat as "controlled transaction
# required". Deploying a registry onto one of them would make every later
# upgrade refuse itself, so they are rejected explicitly.
PACKAGE_GUARD_PATHS = frozenset(
    {
        Path("/var/lib/ai-device/manager"),
        Path("/opt/ai-device/instances/manager"),
    }
)


class HelperError(ValueError):
    pass


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise HelperError("invalid identifier")
    return value


def _path(value):
    path = Path(value)
    if (
        not path.is_absolute()
        or str(path) != str(path.resolve())
        or not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(path))
    ):
        raise HelperError("absolute canonical path without systemd expansions required")
    return path


def _digest(path):
    # Every digest target is an evidence or artifact file. Opening a FIFO, socket
    # or device here would block or read without bound, and several callers reach
    # this before any fenced work starts, so refuse non-regular files up front.
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise HelperError("digest requires a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_regular(path, error):
    """Read instance-owned evidence only when it is a regular file.

    Registration runs as root but reads identity files from the owner's tree.
    A FIFO there would block that read with no error, so check the type first.
    """
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise HelperError(error)
    return Path(path).read_bytes()


def _trusted(path, *, directory=False):
    # Root-owned files in non-writable root-owned ancestors; not an OS sandbox.
    path = Path(path)
    for item in [path, *path.parents]:
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise HelperError("administrator-owned non-writable path required")
    if directory and not path.is_dir():
        raise HelperError("registry must be a directory")


def _read_release_pointer(root):
    """Read the single-line release pointer the launcher trusts.

    Registration follows this pointer so the interpreter it approves is the one
    that will actually start. The value becomes a path component, so it is
    validated here rather than trusted from the file.
    """
    path = root / POINTER
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise HelperError("release pointer must be a regular file")
    value = _read_regular(path, "release pointer must be a regular file").decode("utf-8", "replace")
    release = value.strip()
    if value.count("\n") > 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+!-]*", release):
        raise HelperError("release pointer is not a valid release name")
    return release


def _trusted_instance_interpreter(path, uid, gid):
    """Validate the client interpreter the instance itself will execute.

    Unlike the root helper bootstrap this is not a privilege boundary: the unit
    runs as ``uid`` in both cases, so replacing the file cannot escalate. It must
    still be a regular file with no group/world write, owned by the instance
    account, and reached through ancestors that are not writable by anyone else.
    Otherwise any other local account could swap the code after approval.
    """
    path = Path(path)
    if path.is_symlink():
        raise HelperError("instance interpreter must not be a symlink")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise HelperError("instance interpreter must be a regular file")
    if info.st_mode & 0o022:
        raise HelperError("instance interpreter must not be group/world writable")
    if info.st_uid != uid:
        raise HelperError("instance interpreter must belong to the instance account")
    if not os.access(path, os.X_OK):
        raise HelperError("instance interpreter must be executable")
    # Walk up only while the ancestor belongs to this instance: that is the part
    # of the path the account itself controls. Shared parents above it (such as
    # the home directory policy or /tmp) are not ours to judge here.
    for item in path.parents:
        parent = item.lstat()
        if stat.S_ISLNK(parent.st_mode):
            raise HelperError("instance interpreter path must not contain symlinks")
        if parent.st_uid != uid:
            break
        # Group write is acceptable only when that group is the instance's own
        # group; any other writer could swap the interpreter after approval.
        if parent.st_mode & 0o002:
            raise HelperError("instance interpreter directory must not be world writable")
        if parent.st_mode & 0o020 and parent.st_gid != gid:
            raise HelperError(
                "instance interpreter directory must not be writable by another group"
            )


@contextmanager
def package_lease(registry, *, exclusive=False, existing=False):
    """Shared by start senders; exclusive for package mutation/recovery.

    Lock order: instance lease -> package lease -> instance service lease.
    A sender guard inherits this fd so daemon exit cannot release it early.
    """
    path = Path(registry) / "package-manager.execution.lock"
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | (0 if existing else os.O_CREAT), 0o600)
    try:
        info = os.fstat(fd)
        _trusted(path)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise HelperError("invalid package execution lock")
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HelperError("package or start sender still executing") from exc
        yield fd
    finally:
        os.close(fd)


def _same_json(left, right):
    """Compare JSON values without conflating booleans and numbers."""
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
            right, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError):
        return False


def _valid_admin_approval(value, prepared_sha256):
    return _same_json(value, {"prepared_sha256": prepared_sha256, "approved_by_uid": 0})


def _validate_start_barrier(intent):
    if not isinstance(intent, dict):
        raise HelperError("invalid start intent barrier")
    if "execution_barrier" in intent:
        barrier = intent["execution_barrier"]
        if type(barrier) is not int or barrier not in (1, 2):
            raise HelperError("invalid start intent barrier")


def _job_retired(path):
    """Validate immutable close and retirement evidence; malformed evidence blocks."""
    stem = Path(str(path).removesuffix(".job-intent.json"))
    closed = stem.with_suffix(".job-closed.json")
    retired = stem.with_suffix(".job-retired.json")
    if not os.path.lexists(retired):
        return False
    try:
        for evidence in (path, closed, retired):
            _trusted(evidence)
        intent = json.loads(path.read_bytes())
        if not isinstance(intent, dict):
            return False
        if set(intent) != {
            "schema_version", "state", "close_protocol", "instance_id", "operation_id",
            "unit", "prepared_sha256", "runtime_snapshot_sha256",
        }:
            return False
        instance_id = _identifier(intent["instance_id"])
        operation_id = _identifier(intent["operation_id"])
        if stem.name != f"{len(instance_id)}-{instance_id}--{operation_id}":
            return False
        expected_unit = "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service"
        if intent["unit"] != expected_unit or any(
            not isinstance(intent[key], str) or not re.fullmatch(r"[0-9a-f]{64}", intent[key])
            for key in ("prepared_sha256", "runtime_snapshot_sha256")
        ):
            return False
        close_value = json.loads(closed.read_bytes())
        retire_value = json.loads(retired.read_bytes())
        if any(
            os.path.lexists(stem.with_suffix(suffix))
            for suffix in (".apply-intent.json", ".sender.json", ".outcome.json")
        ):
            return False
        expected_close = {
            "state": "closed_before_apply",
            "job_intent_sha256": _digest(path),
            "retry_allowed": False,
            "fence_retained": True,
        }
        expected_retire = {
            "state": "retired_before_apply",
            "job_intent_sha256": _digest(path),
            "closed_sha256": _digest(closed),
            "old_job_replay_allowed": False,
        }
        return (
            type(intent.get("schema_version")) is int
            and intent.get("schema_version") == 1
            and intent.get("state") == "job_dispatch_intent"
            and intent.get("close_protocol") == "package-lease-v1"
            and _same_json(close_value, expected_close)
            and _same_json(retire_value, expected_retire)
        )
    except (OSError, ValueError, TypeError, HelperError):
        return False


def _pending_package_jobs(directory):
    # A visible retirement does not prove the preceding close was durable.
    # Keep every job fenced until a crash-safe retirement protocol is proven.
    return list(Path(directory).glob("*.job-intent.json"))


def _valid_sender_completion(receipt, intent):
    # A reaped process may exit nonzero or via signal; the integer code is
    # evidence of completion, not evidence that the requested action succeeded.
    return (
        isinstance(receipt, dict)
        and receipt.get("state") == "sender_reaped"
        and _same_json(receipt.get("intent"), intent)
        and type(receipt.get("returncode")) is int
    )


def _require_current_start_barrier(intent):
    """Legacy sender locks alone do not prove the start sender has exited."""
    if not isinstance(intent, dict) or type(intent.get("execution_barrier")) is not int or intent.get("execution_barrier") != 2:
        raise HelperError("legacy start sender completion unproven; manual reconciliation required")


def _require_proven_start_sender_tree():
    """A direct-child receipt does not establish sender descendant completion."""
    raise HelperError("sender process tree completion unproven; recovery disabled")


def _read_start_recovery(path, intent):
    recovery = json.loads(path.read_text())
    if (
        not isinstance(recovery, dict)
        or recovery.get("state") not in ("confirmed_running", "confirmed_stopped")
        or not _same_json(recovery.get("intent"), intent)
        or recovery.get("start_replayed") is not False
    ):
        raise HelperError("invalid recovery evidence")
    return recovery


def _assert_no_package_intent(registry):
    directory = Path(registry) / "updates"
    if not os.path.lexists(directory):
        return
    _trusted(directory, directory=True)
    # An intent is cleared only by a verified terminal receipt: either an
    # explicit administrator retirement of a dead end, or the activation that
    # completed the transaction. Both keep every artifact and bind this exact
    # intent. Without one a dead-end transaction fences the instance for ever,
    # because nothing else removes an intent.
    if any(
        not _retired_transaction(intent)
        for intent in directory.glob("*.apply-intent.json")
    ) or _pending_package_jobs(directory):
        raise HelperError("package transaction blocks start; administrator recovery required")


def _retired_transaction(intent_path):
    """Trust a terminal closure only when it binds this exact intent.

    ``retired_without_replay`` and ``activated`` both end a transaction but record
    different facts, so each keeps its own required fields. Corrupt, untrusted or
    mismatched evidence reports False, so an unpoven receipt keeps the fence
    closed.
    """
    name = intent_path.name
    suffix = ".apply-intent.json"
    if not name.endswith(suffix):
        return False
    digest = _digest(intent_path)
    for receipt in (
        intent_path.with_name(name[: -len(suffix)] + ".retired.json"),
        intent_path.with_name(name[: -len(suffix)] + ".activated.json"),
    ):
        if _terminal_receipt(receipt, digest):
            return True
    return False


def _terminal_receipt(receipt, intent_sha256):
    """Check one closure receipt against the intent it claims to close."""
    if not os.path.lexists(receipt):
        return False
    try:
        _trusted(receipt)
        stored = json.loads(receipt.read_text())
        if not isinstance(stored, dict):
            return False
        state = stored.get("state")
        if state == "retired_without_replay":
            if stored.get("retry_allowed") is not False:
                return False
        elif state != "activated":
            return False
        return stored.get("intent_sha256") == intent_sha256
    except (OSError, ValueError, HelperError):
        return False


def _assert_no_query_supervisor_intent(registry):
    """Caller holds package lease; any visible entry means unknown, not retired."""
    if os.path.lexists(Path(registry) / "query-supervisor.intent.json"):
        raise HelperError("query supervisor intent remains unknown; action refused")


def _save_new(path, value, *, mode=0o600):
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    # Hard-link publication provides no-replace semantics, even with other writers.
    fd, temporary = tempfile.mkstemp(prefix=".helper-", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        temporary.unlink(missing_ok=True)


def render_client_unit(*, instance_id, root, uid, gid, environment_file):
    _identifier(instance_id)
    root, environment_file = _path(root), _path(environment_file)
    if type(uid) is not int or uid <= 0 or type(gid) is not int or gid < 0:
        raise HelperError("explicit non-root UID and GID required")
    return (
        "[Unit]\nDescription=Device native client " + instance_id + "\n"
        "Wants=network-online.target\nAfter=network-online.target\n\n"
        "[Service]\nType=simple\n"
        f"User={uid}\nGroup={gid}\nWorkingDirectory={root}\n"
        f"EnvironmentFile={environment_file}\nExecStart={root}/run client\n"
        "Restart=on-failure\nRestartSec=5\nTimeoutStopSec=30\n"
        "KillMode=control-group\nSendSIGKILL=no\nUMask=0077\nNoNewPrivileges=yes\n\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )










@contextmanager
def _directory_fd(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _instance_service_lease(root):
    """Share the existing supervisor/runner locks, without creating client-owned files as root."""
    with (root / ".installation.lock").open("rb") as runner_lease:
        try:
            fcntl.flock(runner_lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HelperError("instance installation is active") from exc
        upgrades = root / "upgrades"
        supervisor = upgrades / ".supervisor.lock"
        if upgrades.exists():
            if not supervisor.is_file() or supervisor.is_symlink():
                raise HelperError("upgrade state requires administrator reconciliation")
            with supervisor.open("rb") as supervisor_lease:
                try:
                    fcntl.flock(supervisor_lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise HelperError("supervised maintenance is active") from exc
                for path in upgrades.glob("*.json"):
                    state = json.loads(path.read_text()).get("state")
                    if state not in {"verified_local", "failed", "cancelled"}:
                        raise HelperError("nonterminal upgrade blocks service start")
                yield
        else:
            # Existing supervisor provisioning must not race the first startup.
            # The manager lock serializes registered helper actions; recheck below.
            yield
            if upgrades.exists():
                raise HelperError("upgrade state changed during service action; reconcile")


def _trusted_marker(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise HelperError("administrator-owned non-writable management marker required")


def register(registry, unit_dir, *, instance_id, root, username, environment_file, recover=False):
    if os.geteuid() != 0:
        raise HelperError("registration requires administrator privileges")
    root = _path(root)
    registry = _path(registry)
    _trusted(registry, directory=True)
    with package_lease(registry):
        _assert_no_query_supervisor_intent(registry)
        with management_lease(root, registration=True, recovery=recover), (root / ".installation.lock").open("rb") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise HelperError("instance is running or maintenance is active") from exc
            _assert_no_package_intent(registry)
            return _register(
                registry,
                unit_dir,
                instance_id=instance_id,
                root=root,
                username=username,
                environment_file=environment_file,
                recover=recover,
            )


def _register(registry, unit_dir, *, instance_id, root, username, environment_file, recover=False):
    if os.geteuid() != 0:
        raise HelperError("registration requires administrator privileges")
    _identifier(instance_id)
    registry, unit_dir, root = map(_path, (registry, unit_dir, root))
    _trusted(registry, directory=True)
    _trusted(unit_dir, directory=True)
    account = pwd.getpwnam(username)
    environment_file = _path(environment_file)
    _trusted(environment_file)
    if environment_file.stat().st_mode & 0o077:
        raise HelperError("credential environment file must have mode 0600")
    if account.pw_uid == 0 or root.stat().st_uid != account.pw_uid:
        raise HelperError("instance must belong to the selected non-root account")
    if (root / "upgrades").exists():
        raise HelperError("existing upgrade records require an explicit adoption review")
    identity = json.loads(
        _read_regular(root / "instance.json", "instance identity must be a regular file")
    )
    active = json.loads(
        _read_regular(root / "active.json", "active pointer must be a regular file")
    )
    if identity.get("instance_id") != instance_id or active.get("roles") != ["client"]:
        raise HelperError("only the matching single-client instance may be registered")
    runner = root / "run"
    if runner.is_symlink() or not os.access(runner, os.X_OK):
        raise HelperError("stable runner must be a regular executable")
    # X_OK alone does not exclude a FIFO: any file can carry the execute bit.
    if not stat.S_ISREG(runner.lstat().st_mode):
        raise HelperError("stable runner must be a regular executable")
    entry_text = runner.read_text()
    if entry_text == LAUNCHER:
        body = root / "run-runner"
        if body.is_symlink() or not stat.S_ISREG(body.lstat().st_mode):
            raise HelperError("runner body must be a regular file")
        if body.read_text() != RUNNER_BODY:
            raise HelperError("runner body is not the expected program")
        # Follow the pointer the launcher follows, and trust-check that release's
        # interpreter. Switching releases later does not need a new registration:
        # registration approves the instance, and whichever release the pointer
        # names must still pass the same check at start-up.
        release = _read_release_pointer(root)
        interpreter = root / "releases" / release / "runtime" / "bin" / "python3.13"
    else:
        # Legacy instance installed without a private runtime: the entry is a
        # single file whose shebang names the interpreter directly.
        first, _, rest = entry_text.partition("\n")
        if rest != RUNNER_BODY or not first.startswith("#!"):
            raise HelperError("stable runner is not a recognized entrypoint")
        interpreter = Path(first[2:].strip())
    # The client unit runs as the instance account, so this interpreter is not a
    # privilege boundary: swapping it cannot escalate. It still must be a real
    # regular executable that the account cannot rewrite after registration, so
    # the release it belongs to stays the code the administrator approved.
    _trusted_instance_interpreter(interpreter, account.pw_uid, account.pw_gid)
    unit_name = f"ai-device-client-{instance_id}.service"
    unit_path = unit_dir / unit_name
    record_path = registry / (instance_id + ".json")
    if not recover and (unit_path.exists() or record_path.exists()):
        raise HelperError("registration already exists; explicit reconciliation required")
    content = render_client_unit(
        instance_id=instance_id,
        root=root,
        uid=account.pw_uid,
        gid=account.pw_gid,
        environment_file=environment_file,
    )
    baseline = {
        "schema_version": 1,
        "instance_id": instance_id,
        "root": str(root),
        "uid": account.pw_uid,
        "gid": account.pw_gid,
        "unit": unit_name,
        "unit_path": str(unit_path),
        "unit_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "runner_sha256": _digest(runner),
        "identity_sha256": _digest(root / "instance.json"),
        "roles_sha256": _digest(root / "config/roles.json"),
        "environment_file": str(environment_file),
    }
    marker = {
        "schema_version": 2,
        "instance_id": instance_id,
        "registry": str(registry),
        "unit": unit_name,
        "binding": baseline,
    }
    marker_path = root / MARKER
    if recover:
        _trusted_marker(marker_path)
        if not _same_json(json.loads(marker_path.read_text()), marker):
            raise HelperError(
                "registration recovery baseline differs or is legacy; explicit adoption required"
            )
    else:
        _save_new(marker_path, marker, mode=0o644)
    record = {**baseline, "management_sha256": _digest(marker_path)}
    # Verify every existing artifact before adding any missing artifact.
    for path, expected in ((unit_path, content), (record_path, record)):
        if path.exists() or path.is_symlink():
            _trusted(path)
            actual = path.read_text() if path == unit_path else json.loads(path.read_text())
            matches = actual == expected if path == unit_path else _same_json(actual, expected)
            if not matches:
                raise HelperError("registration artifact conflict; no overwrite permitted")
    if not unit_path.exists():
        fd, temporary = tempfile.mkstemp(prefix=".registration-unit-", dir=unit_dir)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(content)
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.fsync(stream.fileno())
            os.link(temporary, unit_path)
            with _directory_fd(unit_dir) as directory_fd:
                os.fsync(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)
    if not record_path.exists():
        _save_new(record_path, record)
    return {
        "instance_id": instance_id,
        "unit": unit_name,
        "started": False,
        "enabled": False,
        "daemon_reload_required": True,
    }


def _unlink_enable_links(unit_dir, unit_name):
    """Remove enable links that point at the unit being deregistered.

    Only ``.wants``/``.requires`` directories directly under the trusted unit
    directory are considered, and a link is removed only when its target names
    this exact unit. Symlinks are never followed while inspecting the tree, so a
    link crafted elsewhere cannot redirect the deletion.
    """
    removed = []
    for link_dir in sorted(unit_dir.glob("*.wants")) + sorted(unit_dir.glob("*.requires")):
        if link_dir.is_symlink() or not link_dir.is_dir():
            continue
        candidate = link_dir / unit_name
        if not candidate.is_symlink():
            continue
        target = os.readlink(candidate)
        if os.path.basename(target) != unit_name:
            continue
        candidate.unlink()
        with _directory_fd(link_dir) as directory_fd:
            os.fsync(directory_fd)
        removed.append(str(candidate))
    return removed


def unregister(registry, unit_dir, *, instance_id):
    """Remove one registration so the payload may be erased.

    This is the supported counterpart of the package erase guard: without it an
    administrator had to delete the unit and registry record by hand. It removes
    only registration artifacts. Instance data, active release and the payload
    stay untouched, matching "uninstall keeps data by default".
    """
    if os.geteuid() != 0:
        raise HelperError("unregistration requires administrator privileges")
    _identifier(instance_id)
    registry, unit_dir = map(_path, (registry, unit_dir))
    _trusted(registry, directory=True)
    _trusted(unit_dir, directory=True)
    unit_name = f"ai-device-client-{instance_id}.service"
    unit_path = unit_dir / unit_name
    record_path = registry / (instance_id + ".json")
    if not record_path.exists() and not unit_path.exists():
        raise HelperError("no registration found for this instance")
    with package_lease(registry, exclusive=True):
        _assert_no_query_supervisor_intent(registry)
        record = json.loads(_read_regular(record_path, "registration record must be a regular file"))
        if record.get("instance_id") != instance_id or record.get("unit") != unit_name:
            raise HelperError("registration record does not match this instance")
        root = _path(record.get("root"))
        # The marker proves this is a native instance; a plain or registration
        # lease rejects it. Deregistration is the supported way to drop it.
        with management_lease(root, deregistration=True), (root / ".installation.lock").open("rb") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise HelperError("instance is running or maintenance is active") from exc
            _assert_no_package_intent(registry)
            # Stop only on a positive observation. An unreachable systemd is not
            # evidence that the unit is idle, so it must not silently skip.
            adapter = Systemd()
            observation = adapter.query(unit_name)
            if observation["ActiveState"] not in {"inactive", "failed"}:
                raise HelperError("stop the instance service before unregistering")
            if not unit_path.exists():
                raise HelperError("registration unit is missing; explicit reconciliation required")
            _trusted(unit_path)
            # Drop enable links before the unit itself. Removing the unit first
            # would leave a dangling link under .wants/, which systemd reports as
            # a not-found unit and which no later command can clean up.
            removed_links = _unlink_enable_links(unit_dir, unit_name)
            # Remove the unit first: a leftover unit would keep the erase guard
            # closed, while a leftover record alone is still recoverable.
            unit_path.unlink()
            with _directory_fd(unit_dir) as directory_fd:
                os.fsync(directory_fd)
            record_path.unlink()
            with _directory_fd(registry) as directory_fd:
                os.fsync(directory_fd)
    return {
        "instance_id": instance_id,
        "unit": unit_name,
        "removed_unit": str(unit_path),
        "removed_record": str(record_path),
        "removed_enable_links": removed_links,
        "data_preserved": True,
        "daemon_reload_required": True,
    }


class Systemd:
    def __init__(self, executable="/usr/bin/systemctl"):
        self.executable = executable

    def query(self, unit):
        output = subprocess.run(
            [
                self.executable,
                "show",
                unit,
                "--all",
                "--no-pager",
                "--property=ActiveState,SubState,MainPID,Job,User,Group,FragmentPath,ExecStart",
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        ).stdout
        expected = {"ActiveState", "SubState", "MainPID", "Job", "User", "Group", "FragmentPath", "ExecStart"}
        if not isinstance(output, str) or len(output) > 16384:
            raise HelperError("invalid systemd unit observation")
        fields = {}
        for line in output.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key not in expected or key in fields:
                raise HelperError("invalid systemd unit observation")
            fields[key] = value
        if set(fields) != expected:
            raise HelperError("incomplete systemd unit observation")
        return fields

    def cancel_job(self, job):
        if not str(job).isdigit() or int(job) <= 0:
            raise HelperError("invalid systemd job")
        subprocess.run(
            [self.executable, "cancel", str(job)],
            check=True,
            capture_output=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )

    def start(self, unit, *, lease_fd=None, receipt_path=None, intent=None, package_fd=None):
        if lease_fd is None:
            raise HelperError("start execution lease required")
        # The standard-library guard owns the inherited flock until systemctl
        # exits and is reaped, even if the daemon dies. Do not rely on systemctl
        # preserving an unknown fd. Its own timeout kills/reaps only its child.
        guard = """import json,os,subprocess,sys,tempfile
from pathlib import Path
receipt=Path(sys.argv[1]); intent=json.loads(sys.argv[2])
try:
    result=subprocess.run(sys.argv[3:],timeout=120,check=False)
    code=result.returncode
except subprocess.TimeoutExpired:
    code=124
value={"state":"sender_reaped","intent":intent,"returncode":code}
fd,temporary=tempfile.mkstemp(prefix=".sender-",dir=receipt.parent)
with os.fdopen(fd,"w") as stream:
    json.dump(value,stream,sort_keys=True); stream.flush(); os.fsync(stream.fileno())
os.link(temporary,receipt); os.unlink(temporary)
fd=os.open(receipt.parent,os.O_RDONLY|os.O_DIRECTORY)
os.fsync(fd); os.close(fd)
sys.exit(code)
"""
        if receipt_path is None or intent is None:
            raise HelperError("sender receipt target and intent required")
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                guard,
                str(receipt_path),
                json.dumps(intent),
                self.executable,
                "start",
                unit,
            ],
            check=True,
            capture_output=True,
            pass_fds=(lease_fd,) if package_fd is None else (lease_fd, package_fd),
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )


class Helper:
    @contextmanager
    def _execution_lease(self, instance_id, *, existing=False):
        path = self.registry / f"execution-{instance_id}.lock"
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | (0 if existing else os.O_CREAT), 0o600)
        try:
            _trusted(path)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise HelperError("old start sender is still active") from exc
            yield fd
        finally:
            os.close(fd)

    def __init__(self, registry, *, adapter=None):
        self.registry = _path(registry)
        _trusted(self.registry, directory=True)
        self.adapter = adapter or Systemd()

    def _binding(self, instance_id):
        path = self.registry / (instance_id + ".json")
        _trusted(path)
        record = json.loads(path.read_text())
        if record.get("schema_version") != 1 or record.get("instance_id") != instance_id:
            raise HelperError("invalid registered binding")
        unit = _path(record["unit_path"])
        _trusted(unit)
        if unit.name != f"ai-device-client-{instance_id}.service" or record["unit"] != unit.name:
            raise HelperError("unit binding mismatch")
        if _digest(unit) != record["unit_sha256"]:
            raise HelperError("unit changed; administrator reconciliation required")
        root = _path(record["root"])
        if (
            record.get("management_sha256") is None
            or _digest(root / MARKER) != record["management_sha256"]
        ):
            raise HelperError(
                "native management binding missing or changed; explicit adoption required"
            )
        if root.stat().st_uid != record["uid"] or record["uid"] <= 0:
            raise HelperError("instance owner changed")
        for relative, key in [
            ("run", "runner_sha256"),
            ("instance.json", "identity_sha256"),
            ("config/roles.json", "roles_sha256"),
        ]:
            if _digest(root / relative) != record[key]:
                raise HelperError("instance binding changed")
        _trusted(_path(record["environment_file"]))
        return record

    def _snapshot(self, record):
        snapshot = self.adapter.query(record["unit"])
        expected = str(Path(record["root"]) / "run")
        match = re.fullmatch(
            r"\{ path=([^ ;]+) ; argv\[\]=([^;]+) ; .*\}", snapshot.get("ExecStart", "")
        )
        if (
            snapshot.get("User") != str(record["uid"])
            or snapshot.get("Group") != str(record["gid"])
            or snapshot.get("FragmentPath") != record["unit_path"]
            or not match
            or match[1] != expected
            or match[2].strip() != expected + " client"
        ):
            raise HelperError("loaded systemd binding differs from registered unit")
        return {k: snapshot.get(k) for k in ("ActiveState", "SubState", "MainPID", "Job")}

    @contextmanager
    def _lease(self, instance_id):
        with (self.registry / (instance_id + ".lock")).open("a+b") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise HelperError("instance management busy") from exc
            yield





































def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for command in ("register", "recover-registration"):
        provision = modes.add_parser(command)
        for name in ("unit-dir", "root", "environment-file"):
            provision.add_argument("--" + name, type=Path, required=True)
        provision.add_argument("--registry", type=Path, default=REGISTRY)
        provision.add_argument("--instance-id", required=True)
        provision.add_argument("--username", required=True)
    removal = modes.add_parser("unregister")
    removal.add_argument("--registry", type=Path, default=REGISTRY)
    removal.add_argument("--unit-dir", type=Path, required=True)
    removal.add_argument("--instance-id", required=True)
    args = vars(parser.parse_args())
    mode = args.pop("mode")
    try:
        if mode in {"register", "recover-registration"}:
            print(
                json.dumps(register(**args, recover=mode == "recover-registration"), sort_keys=True)
            )
        else:
            print(json.dumps(unregister(**args), sort_keys=True))
    except (HelperError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"helper refused: {exc}\n")


if __name__ == "__main__":
    main()
