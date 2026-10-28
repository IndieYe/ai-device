"""Linux native service helper: administrator registration and bounded local IPC.

Only query/start/reconcile/resolve-running are implemented. No stop, arbitrary argv, installation,
package activation or automatic replay. Run the daemon via its dedicated root unit.
"""

import argparse
import array
import errno
import fcntl
import hashlib
import json
import os
import pwd
import re
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

from .management_lock import MARKER, management_lease

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MAX_REQUEST = 4096
ACTIONS = {"query", "start", "reconcile", "resolve-running", "resolve-stopped"}
RUNTIME_ROOT = Path("/run")


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
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trusted(path, *, directory=False):
    # Root-owned files in non-writable root-owned ancestors; not an OS sandbox.
    path = Path(path)
    for item in [path, *path.parents]:
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise HelperError("administrator-owned non-writable path required")
    if directory and not path.is_dir():
        raise HelperError("registry must be a directory")


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
    # Until activation/repair has a verified terminal protocol, no intent is
    # automatically cleared, including failed or recovered package executions.
    if any(directory.glob("*.apply-intent.json")) or _pending_package_jobs(directory):
        raise HelperError("package transaction blocks start; administrator recovery required")


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


def render_helper_unit(*, bootstrap_python, registry, socket_path, group, candidate_version=None, candidate_sha256=None):
    python, registry, socket_path = map(_path, (bootstrap_python, registry, socket_path))
    if type(group) is not int or group < 0:
        raise HelperError("explicit socket group required")
    if candidate_version is None:
        if candidate_sha256 is not None:
            raise HelperError("candidate digest requires version")
        entry = "device_runtime.native_helper serve"
        version_arg = ""
    else:
        _identifier(candidate_version)
        if not isinstance(candidate_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_sha256):
            raise HelperError("candidate snapshot digest required")
        entry = "device_runtime.native_bootstrap"
        version_arg = f" --version {candidate_version} --snapshot-sha256 {candidate_sha256}"
    return (
        "[Unit]\nDescription=Device bounded native management helper\n"
        "After=local-fs.target\n\n[Service]\nType=simple\nUser=root\nGroup=root\n"
        f"ExecStart={python} -I -B -m {entry} "
        f"--registry {registry} --socket {socket_path} --group {group}{version_arg}\n"
        "# The start guard owns inherited leases until its sender is reaped.\n"
        "KillMode=process\nUMask=0077\nNoNewPrivileges=yes\nRestart=no\n"
        "# No ExecStartPre unlink: an orphan socket requires explicit reconciliation.\n"
        "\n[Install]\nWantedBy=multi-user.target\n"
    )


def deploy_helper(*, bootstrap_python, registry, unit_dir, runtime_dir, group, candidate_version=None):
    """Explicit administrator provisioning only; never reload, enable or start."""
    if sys.platform != "linux" or os.geteuid() != 0:
        raise HelperError("helper deployment requires Linux administrator identity")
    python, registry, unit_dir, runtime_dir = map(
        _path, (bootstrap_python, registry, unit_dir, runtime_dir)
    )
    if candidate_version is not None:
        _identifier(candidate_version)
    _trusted(python)
    if not python.is_file() or not os.access(python, os.X_OK):
        raise HelperError("executable bootstrap required")
    _trusted(unit_dir, directory=True)
    if runtime_dir.parent != RUNTIME_ROOT:
        raise HelperError("runtime directory must be directly beneath /run")
    unit = unit_dir / "ai-device-native-helper.service"
    if unit.exists() or unit.is_symlink():
        raise HelperError("helper unit already exists; explicit reconciliation required")
    candidate_sha256 = None
    if candidate_version is not None:
        from . import native_bootstrap

        if python != native_bootstrap.TRUSTED_HELPER_BOOTSTRAP / "bin/python3.13":
            raise HelperError("independent bootstrap required for candidate deployment")
        _trusted(registry, directory=True)
        with package_lease(registry, exclusive=True, existing=True):
            evidence = native_bootstrap.read_helper_candidate(registry / "bootstrap-candidates", candidate_version)
            candidate_sha256 = evidence["snapshot_sha256"]
    content = render_helper_unit(
        bootstrap_python=python, registry=registry, socket_path=runtime_dir / "helper.sock",
        group=group, candidate_version=candidate_version, candidate_sha256=candidate_sha256,
    )
    for directory in (registry, runtime_dir):
        if not directory.exists():
            _trusted(directory.parent, directory=True)
            directory.mkdir(mode=0o755)
        _trusted(directory, directory=True)
    # RuntimeDirectory is managed by systemd across host boots. Preserve sockets
    # across daemon failures: never silently unlink an unknown existing endpoint.
    content = content.replace(
        "UMask=0077\n",
        f"RuntimeDirectory={runtime_dir.name}\n"
        "RuntimeDirectoryMode=0755\nRuntimeDirectoryPreserve=yes\nUMask=0077\n",
    )
    fd, temporary = tempfile.mkstemp(prefix=".helper-unit-", dir=unit_dir)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.link(temporary, unit)
        with _directory_fd(unit_dir) as directory_fd:
            os.fsync(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"unit": str(unit), "started": False, "enabled": False, "daemon_reload_required": True}


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
    with (
        package_lease(registry),
        management_lease(root, registration=True, recovery=recover),
        (root / ".installation.lock").open("rb") as lease,
    ):
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
    identity = json.loads((root / "instance.json").read_text())
    active = json.loads((root / "active.json").read_text())
    if identity.get("instance_id") != instance_id or active.get("roles") != ["client"]:
        raise HelperError("only the matching single-client instance may be registered")
    runner = root / "run"
    if runner.is_symlink() or not os.access(runner, os.X_OK):
        raise HelperError("stable runner must be a regular executable")
    first = runner.read_text().splitlines()[0]
    if not first.startswith("#!/") or first.startswith("#!/usr/bin/env"):
        raise HelperError("native runner requires an absolute packaged interpreter")
    interpreter = _path(first[2:])
    _trusted(interpreter)
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

    def handle(self, request, *, peer_uid):
        if not isinstance(request, dict) or set(request) != {
            "action",
            "instance_id",
            "operation_id",
        }:
            raise HelperError("request accepts only action, instance_id and operation_id")
        action = request["action"]
        if not isinstance(action, str) or action not in ACTIONS:
            raise HelperError("unsupported helper action")
        instance_id = _identifier(request["instance_id"])
        operation_id = _identifier(request["operation_id"])
        record = self._binding(instance_id)
        if type(peer_uid) is not int or peer_uid not in (0, record["uid"]):
            raise HelperError("peer is not the registered owner")
        with self._lease(instance_id):
            snapshot = self._snapshot(record)
            result = {
                "instance_id": instance_id,
                "operation_id": operation_id,
                "observation": snapshot,
            }
            recovery_path = self.registry / f"recovery-{instance_id}-{operation_id}.json"
            if action in {"resolve-running", "resolve-stopped"}:
                intent_path = self.registry / f"op-{instance_id}-{operation_id}.json"
                result_path = self.registry / f"result-{instance_id}-{operation_id}.json"
                expected = {
                    "action": "start",
                    "instance_id": instance_id,
                    "operation_id": operation_id,
                    "unit_sha256": record["unit_sha256"],
                }
                stored_intent = (
                    json.loads(intent_path.read_text()) if intent_path.exists() else None
                )
                if stored_intent is not None:
                    _validate_start_barrier(stored_intent)
                if stored_intent and stored_intent.get("execution_barrier") in (1, 2):
                    expected["execution_barrier"] = stored_intent["execution_barrier"]
                if stored_intent != expected:
                    raise HelperError("matching start intent required for recovery")
                _require_current_start_barrier(stored_intent)
                _require_proven_start_sender_tree()
                if recovery_path.exists():
                    return {**result, "recovery": _read_start_recovery(recovery_path, expected)}
                if (
                    result_path.exists()
                    and json.loads(result_path.read_text()).get("state") != "unknown"
                ):
                    raise HelperError("only an unknown start requires recovery")
                with (
                    _instance_service_lease(Path(record["root"])),
                    self._execution_lease(instance_id, existing=True)
                    if expected.get("execution_barrier") in (1, 2)
                    else nullcontext(),
                ):
                    if expected.get("execution_barrier") == 2:
                        receipt_path = self.registry / f"sender-{instance_id}-{operation_id}.json"
                        if not receipt_path.exists():
                            raise HelperError("sender completion receipt missing; remains unknown")
                        _trusted(receipt_path)
                        receipt = json.loads(receipt_path.read_text())
                        if not _valid_sender_completion(receipt, expected):
                            raise HelperError("sender completion receipt mismatch; remains unknown")
                    if action == "resolve-stopped":
                        if expected.get("execution_barrier") != 2:
                            raise HelperError(
                                "legacy intent has no sender barrier; manual reconciliation required"
                            )
                        observation = self._snapshot(record)
                        if (
                            observation["ActiveState"] not in {"inactive", "failed"}
                            or observation["MainPID"] != "0"
                        ):
                            raise HelperError(
                                "nonrunning service required; no running client will be stopped"
                            )
                        job = observation["Job"]
                        if job not in ("", "0"):
                            if not isinstance(job, str) or not job.strip():
                                raise HelperError("unknown systemd job; remains unknown")
                            self.adapter.cancel_job(job.split()[0])
                        observation = self._snapshot(record)
                        if (
                            observation["ActiveState"] not in {"inactive", "failed"}
                            or observation["SubState"] not in {"dead", "failed"}
                            or observation["MainPID"] != "0"
                            or observation["Job"] not in ("", "0")
                        ):
                            raise HelperError("service or job not quiescent; remains unknown")
                        recovery = {
                            "state": "confirmed_stopped",
                            "intent": expected,
                            "observation": observation,
                            "start_replayed": False,
                        }
                        _save_new(recovery_path, recovery)
                        return {**result, "recovery": recovery}
                    observation = self._snapshot(record)
                    if (
                        observation["ActiveState"] != "active"
                        or observation["SubState"] != "running"
                        or not str(observation["MainPID"]).isdigit()
                        or int(observation["MainPID"]) <= 1
                        or observation["Job"] not in ("", "0")
                    ):
                        raise HelperError(
                            "stable running service without a pending job required; remains unknown"
                        )
                    recovery = {
                        "state": "confirmed_running",
                        "intent": expected,
                        "observation": observation,
                        "start_replayed": False,
                    }
                    _save_new(recovery_path, recovery)
                return {**result, "recovery": recovery}
            if action != "start":
                path = self.registry / f"op-{instance_id}-{operation_id}.json"
                result["intent"] = json.loads(path.read_text()) if path.exists() else None
                stored_result = self.registry / f"result-{instance_id}-{operation_id}.json"
                if result["intent"] is not None:
                    intent = result["intent"]
                    if (
                        not isinstance(intent, dict)
                        or intent.get("action") != "start"
                        or intent.get("instance_id") != instance_id
                        or intent.get("operation_id") != operation_id
                    ):
                        raise HelperError("operation journal identity differs")
                elif stored_result.exists() or recovery_path.exists():
                    raise HelperError("operation journal intent missing")
                result["result"] = (
                    json.loads(stored_result.read_text()) if stored_result.exists() else None
                )
                result["recovery"] = (
                    _read_start_recovery(recovery_path, result["intent"])
                    if recovery_path.exists()
                    else None
                )
                return result
            intent_path = self.registry / f"op-{instance_id}-{operation_id}.json"
            result_path = self.registry / f"result-{instance_id}-{operation_id}.json"
            intent = {
                "action": "start",
                "instance_id": instance_id,
                "operation_id": operation_id,
                "unit_sha256": record["unit_sha256"],
                "execution_barrier": 2,
            }
            if intent_path.exists():
                existing_intent = json.loads(intent_path.read_text())
                _validate_start_barrier(existing_intent)
                if "execution_barrier" not in existing_intent:
                    intent.pop("execution_barrier")
                else:
                    intent["execution_barrier"] = existing_intent["execution_barrier"]
                if existing_intent != intent:
                    raise HelperError("operation identity conflict")
                return {
                    **result,
                    "result": json.loads(result_path.read_text())
                    if result_path.exists()
                    else {"state": "unknown"},
                    "replayed": False,
                    "recovery": _read_start_recovery(recovery_path, existing_intent)
                    if recovery_path.exists()
                    else None,
                }
            for pending in self.registry.glob(f"op-{instance_id}-*.json"):
                pending_intent = json.loads(pending.read_text())
                _validate_start_barrier(pending_intent)
                result_file = pending.with_name(pending.name.replace("op-", "result-", 1))
                state = None
                if result_file.exists():
                    stored = json.loads(result_file.read_text())
                    if not isinstance(stored, dict) or stored.get("state") not in (
                        "started", "already_active", "unknown"
                    ):
                        raise HelperError("invalid start result blocks a new start")
                    state = stored["state"]
                if state == "started":
                    _require_proven_start_sender_tree()
                if state in (None, "unknown"):
                    recovered = pending.with_name(pending.name.replace("op-", "recovery-", 1))
                    if not recovered.exists():
                        raise HelperError(
                            "unconfirmed operation blocks a new start; reconcile first"
                        )
                    _require_current_start_barrier(pending_intent)
                    _require_proven_start_sender_tree()
                    _read_start_recovery(recovered, pending_intent)
            if snapshot["ActiveState"] not in {"inactive", "failed", "active"}:
                raise HelperError("service transition requires read-only reconciliation")
            with (
                self._execution_lease(instance_id) as execution_fd,
                package_lease(self.registry) as package_fd,
            ):
                _assert_no_package_intent(self.registry)
                _save_new(intent_path, intent)
                outcome = {"state": "already_active"}
                if snapshot["ActiveState"] != "active":
                    try:
                        with _instance_service_lease(Path(record["root"])):
                            self.adapter.start(
                                record["unit"],
                                lease_fd=execution_fd,
                                package_fd=package_fd,
                                receipt_path=self.registry
                                / f"sender-{instance_id}-{operation_id}.json",
                                intent=intent,
                            )
                            observation = self._snapshot(record)
                        outcome = {
                            "state": "started"
                            if observation["ActiveState"] == "active"
                            else "unknown",
                            "observation": observation,
                        }
                    except (OSError, subprocess.SubprocessError, HelperError):
                        outcome = {"state": "unknown"}
                _save_new(result_path, outcome)
                return {**result, "result": outcome, "replayed": False}


@contextmanager
def _daemon_anchor(registry):
    """Serialize daemon and socket recovery on the stable registry directory.

    The administrator must keep the registry, its ancestors and mount stable
    while a daemon or unresolved transaction exists. This cooperative lock
    cannot enforce that operational precondition or identify old helpers.
    """
    registry = _path(registry)
    _trusted(registry, directory=True)
    fd = os.open(registry, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HelperError("helper daemon or recovery is already active") from exc
        try:
            locked = os.fstat(fd)
            current = registry.lstat()
            _trusted(registry, directory=True)
        except OSError as exc:
            raise HelperError("daemon anchor changed during acquisition") from exc
        if (
            not stat.S_ISDIR(locked.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (locked.st_dev, locked.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise HelperError("daemon anchor changed during acquisition")
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _daemon_lease(registry):
    with _daemon_lease_pair(registry) as (_, fd):
        yield fd


@contextmanager
def _daemon_lease_pair(registry):
    """Own both daemon lock descriptions for the lifetime of this context.

    This internal interface does not prove locks remain held, authorize a
    child or operation, or provide supervision. Callers must not unlock or
    close the descriptors and must control every fork inheritance.
    """
    registry = _path(registry)
    with _daemon_anchor(registry) as anchor, _daemon_file_lease(registry) as fd:
        yield anchor, fd


@contextmanager
def _daemon_file_lease(registry):
    path = registry / "helper-daemon.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        _trusted(path)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HelperError("helper daemon or recovery is already active") from exc
        # Verify the locked description is still the trusted path after locking.
        # This detects acquisition-time drift, not later replacement or unlock.
        try:
            locked = os.fstat(fd)
            current = path.lstat()
            _trusted(path)
        except OSError as exc:
            raise HelperError("daemon lease path changed during acquisition") from exc
        if (
            not stat.S_ISREG(locked.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or locked.st_mode & 0o022
            or (locked.st_dev, locked.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise HelperError("daemon lease path changed during acquisition")
        # The caller may retain this exact open-file description across fork.
        # An fd alone does not prove a live lock or authorize an operation.
        yield fd
    finally:
        os.close(fd)


def recover_socket(registry, socket_path):
    """Administrator-only explicit stale endpoint cleanup, never operation replay."""
    if sys.platform != "linux" or os.geteuid() != 0:
        raise HelperError("socket recovery requires Linux administrator identity")
    socket_path = _path(socket_path)
    _trusted(socket_path.parent, directory=True)
    with _daemon_lease(registry):
        try:
            before = socket_path.lstat()
        except FileNotFoundError:
            return {"removed": False, "reason": "absent"}
        if not stat.S_ISSOCK(before.st_mode) or before.st_uid != 0:
            raise HelperError("only a root-owned Unix socket may be recovered")
        with socket.socket(socket.AF_UNIX) as probe:
            probe.settimeout(1)
            try:
                probe.connect(str(socket_path))
            except OSError as exc:
                if exc.errno != errno.ECONNREFUSED:
                    raise HelperError("endpoint status uncertain; recovery refused") from exc
            else:
                raise HelperError("endpoint is listening; recovery refused")
        after = socket_path.lstat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise HelperError("endpoint changed; recovery refused")
        socket_path.unlink()
        with _directory_fd(socket_path.parent) as directory_fd:
            os.fsync(directory_fd)
        return {"removed": True, "operations_changed": False}


def serve(registry, socket_path, *, group):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise HelperError("helper daemon requires Linux administrator identity")
    with _daemon_lease(registry):
        _serve(registry, socket_path, group=group)


def _serve(registry, socket_path, *, group):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise HelperError("helper daemon requires Linux administrator identity")
    helper = Helper(registry)
    socket_path = _path(socket_path)
    _trusted(socket_path.parent, directory=True)
    # Existing socket may be in use or orphaned; never unlink it automatically.
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(socket_path))
        os.chown(socket_path, 0, group)
        os.chmod(socket_path, 0o660)
        server.listen(8)
        while True:
            connection, _ = server.accept()
            with connection:
                deadline = time.monotonic() + 5
                connection.settimeout(5)
                try:
                    uid = struct.unpack(
                        "3i",
                        connection.getsockopt(
                            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                        ),
                    )[1]
                    data = bytearray()
                    while not data.endswith(b"\n"):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise HelperError("request deadline exceeded")
                        connection.settimeout(remaining)
                        chunk = connection.recv(min(1024, MAX_REQUEST + 1 - len(data)))
                        if not chunk:
                            raise HelperError("incomplete request")
                        data.extend(chunk)
                        if len(data) > MAX_REQUEST:
                            raise HelperError("request too large")
                    if time.monotonic() >= deadline:
                        raise HelperError("request deadline exceeded")
                    connection.settimeout(5)
                    result = helper.handle(json.loads(data), peer_uid=uid)
                    response = {"ok": True, "result": result}
                except (HelperError, ValueError, KeyError, OSError, subprocess.SubprocessError):
                    # Never return command stderr or environment/credential content.
                    response = {"ok": False, "error": "helper_request_refused"}
                try:
                    connection.settimeout(5)
                    connection.sendall(json.dumps(response).encode() + b"\n")
                except OSError:
                    pass  # A lost response must not cause the action to replay.


def _receive_root_credential_frame(connection, *, deadline):
    """Observe one root-sent frame; not a handler, code, or invocation proof.

    The listener must enable SO_PASSCRED before any data can be queued. This
    function cannot verify that historical precondition. The caller owns the
    connection and must close it after this single frame, including on failure.
    No business action or authorization is performed here.
    """
    if sys.platform != "linux" or not all(
        hasattr(socket, name) for name in ("SO_PASSCRED", "SCM_CREDENTIALS", "SCM_RIGHTS", "MSG_CMSG_CLOEXEC")
    ):
        raise HelperError("per-message credentials unavailable")
    try:
        if connection.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) != 1:
            raise HelperError("per-message credentials not enabled")
        data = bytearray()
        sender = None
        credential_size = struct.calcsize("3i")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HelperError("request deadline exceeded")
            connection.settimeout(remaining)
            chunk, controls, flags, _ = connection.recvmsg(
                min(1024, MAX_REQUEST + 1 - len(data)),
                socket.CMSG_SPACE(credential_size) + socket.CMSG_SPACE(256),
                socket.MSG_CMSG_CLOEXEC,
            )
            # The kernel installs delivered SCM_RIGHTS even on rejected records.
            close_failed = False
            for level, kind, payload in controls:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    descriptors = array.array("i")
                    descriptors.frombytes(payload[:len(payload) - len(payload) % descriptors.itemsize])
                    for fd in descriptors:
                        try:
                            os.close(fd)
                        except OSError:
                            close_failed = True
            if close_failed:
                raise HelperError("cannot close unexpected descriptors")
            if flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC) or not chunk:
                raise HelperError("truncated or incomplete request")
            if len(controls) != 1:
                raise HelperError("one sender credential required")
            level, kind, payload = controls[0]
            if level != socket.SOL_SOCKET or kind != socket.SCM_CREDENTIALS or len(payload) != credential_size:
                raise HelperError("invalid sender credential")
            identity = struct.unpack("3i", payload)
            if identity[0] <= 0 or identity[1] != 0 or (sender is not None and sender != identity):
                raise HelperError("sender changed or is not root")
            sender = identity
            data.extend(chunk)
            if len(data) > MAX_REQUEST:
                raise HelperError("request too large")
            if b"\n" in data:
                if not data.endswith(b"\n") or data.count(b"\n") != 1:
                    raise HelperError("trailing or multiple request frames")
                if time.monotonic() >= deadline:
                    raise HelperError("request deadline exceeded")
                return bytes(data), sender
    except OSError as exc:
        raise HelperError("credential frame receive failed") from exc


def _serve_credential_query_once(service, connection, *, deadline):
    """Unwired one-frame root query fixture; not supervisor or dispatch authorization.

    Caller must enable SO_PASSCRED on the listener before any bytes are queued.
    This owns and closes the accepted connection after exactly one response.
    Only query is permitted; an observed sender is not a trusted handler identity.
    """
    def pairs_no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise HelperError("duplicate request field")
            result[key] = value
        return result

    with connection:
        try:
            frame, sender = _receive_root_credential_frame(connection, deadline=deadline)
            if sender[1] != 0:
                raise HelperError("root sender required")
            request = json.loads(frame, object_pairs_hook=pairs_no_duplicates)
            if not isinstance(request, dict) or set(request) != {
                "action", "instance_id", "operation_id"
            } or request["action"] != "query":
                raise HelperError("only query is permitted")
            _identifier(request["instance_id"])
            _identifier(request["operation_id"])
            response = {"ok": True, "result": service.handle(request, peer_uid=sender[1])}
        except (HelperError, ValueError, KeyError, OSError, subprocess.SubprocessError):
            response = {"ok": False, "error": "helper_request_refused"}
        try:
            connection.settimeout(5)
            connection.sendall(json.dumps(response).encode() + b"\n")
        except OSError:
            pass  # A lost response must not cause a second handler call.


def _serve_credential_queries(registry, socket_path, *, group):
    """Internal root-only query listener; not wired into the daemon or supervisor.

    It does not authenticate the executing handler or authorize mutations.
    The caller must ensure the stable-registry administrative precondition.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise HelperError("query listener requires Linux administrator identity")
    if not all(hasattr(socket, name) for name in (
        "SO_PASSCRED", "SCM_CREDENTIALS", "SCM_RIGHTS", "MSG_CMSG_CLOEXEC"
    )):
        raise HelperError("per-message credentials unavailable")
    with _daemon_lease(registry):
        _serve_credential_queries_locked(registry, socket_path, group=group)


def _serve_credential_queries_locked(registry, socket_path, *, group):
    """Internal query loop; caller owns both daemon locks for its entire lifetime.

    This function cannot verify that precondition or authenticate a supervisor;
    it must not be treated as an authorization or public service entry point.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise HelperError("query listener requires Linux administrator identity")
    if not all(hasattr(socket, name) for name in (
        "SO_PASSCRED", "SCM_CREDENTIALS", "SCM_RIGHTS", "MSG_CMSG_CLOEXEC"
    )):
        raise HelperError("per-message credentials unavailable")
    service = Helper(registry)
    socket_path = _path(socket_path)
    _trusted(socket_path.parent, directory=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        server.bind(str(socket_path))
        os.chown(socket_path, 0, group)
        os.chmod(socket_path, 0o600)
        server.listen(8)
        while True:
            connection, _ = server.accept()
            _serve_credential_query_once(service, connection, deadline=time.monotonic() + 5)


@contextmanager
def _socket_peer_process(connection):
    """Pin a Linux Unix-socket peer for observation, never authorize dispatch.

    SO_PEERPIDFD (Linux 6.5+) pins the kernel-reported socket peer without
    the PID reuse race in pidfd_open(peer_pid). The peer can be a socket
    creator rather than the process handling a particular message. Unsupported
    kernels and peers that exit during observation fail closed. This does not
    prove the handler, loaded code, or prevent a new daemon from starting.
    """
    import select

    if sys.platform != "linux":
        raise HelperError("Linux peer process identity required")
    fd = None
    try:
        try:
            raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            pid, uid, gid = struct.unpack("3i", raw)
            if pid <= 1 or uid != 0:
                raise HelperError("unexpected helper peer credentials")
            # Python may not expose SO_PEERPIDFD even when the kernel supports it.
            raw_fd = connection.getsockopt(socket.SOL_SOCKET, getattr(socket, "SO_PEERPIDFD", 77), 4)
            fd = struct.unpack("i", raw_fd)[0]
            if fd < 0:
                raise HelperError("invalid helper peer pidfd")
            os.set_inheritable(fd, False)
            with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as info:
                fields = dict(line.rstrip().split(":", 1) for line in info if ":" in line)
            if fields.get("Pid", "").strip() != str(pid):
                raise HelperError("helper peer pidfd does not match credentials")
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            if poller.poll(0):
                raise HelperError("helper peer exited during observation")
        except (OSError, ValueError, struct.error) as exc:
            if isinstance(exc, HelperError):
                raise
            raise HelperError("cannot establish helper peer process identity") from exc
        yield {"pid": pid, "uid": uid, "gid": gid, "pidfd": fd}
        if poller.poll(0):
            raise HelperError("helper peer exited during observation")
    finally:
        if fd is not None and fd >= 0:
            os.close(fd)


def _helper_unit_observation(*, runner=subprocess.run):
    """Read the fixed helper unit; duplicate, missing or uncertain fields fail closed."""
    try:
        result = runner(
            [
                "/usr/bin/systemctl", "show", "ai-device-native-helper.service", "--no-pager",
                "--property=LoadState,ActiveState,SubState,MainPID,InvocationID,FragmentPath",
            ],
            check=True, text=True, capture_output=True, timeout=10,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )
        if len(result.stdout) > 16384:
            raise HelperError("helper unit observation too large")
        fields = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in fields:
                raise HelperError("invalid helper unit observation")
            fields[key] = value
        expected = {"LoadState", "ActiveState", "SubState", "MainPID", "InvocationID", "FragmentPath"}
        if set(fields) != expected:
            raise HelperError("incomplete helper unit observation")
        if (
            fields["LoadState"] != "loaded"
            or fields["ActiveState"] != "active"
            or fields["SubState"] != "running"
            or not fields["MainPID"].isdigit()
            or int(fields["MainPID"]) <= 1
            or not re.fullmatch(r"[0-9a-f]{32}", fields["InvocationID"])
            or not fields["FragmentPath"].startswith("/")
        ):
            raise HelperError("helper unit is not stably running")
        return fields
    except (OSError, subprocess.SubprocessError) as exc:
        raise HelperError("cannot observe helper unit") from exc


@contextmanager
def scoped_helper_unit_peer(connection, *, runner=subprocess.run):
    """Pin a socket peer across a read-only observation scope, not authorization.

    A successful exit requires the same systemd invocation and a live pidfd.
    The caller must not commit an irreversible action within this scope: a
    changed invocation may only be detected when the scope exits.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise HelperError("Linux administrator required")
    with _socket_peer_process(connection) as peer:
        before = _helper_unit_observation(runner=runner)
        if int(before["MainPID"]) != peer["pid"]:
            raise HelperError("helper unit and socket peer differ or changed")
        yield {
            "state": "helper_unit_peer_observed_not_authorized",
            "pid": peer["pid"],
            "invocation_id": before["InvocationID"],
            "fragment_path": before["FragmentPath"],
            "loaded_code_verified": False,
            "dispatch_allowed": False,
        }
        if before != _helper_unit_observation(runner=runner):
            raise HelperError("helper unit and socket peer differ or changed")


def observe_helper_unit_peer(connection, *, runner=subprocess.run):
    """Take an instantaneous read-only observation, not loaded-code proof."""
    with scoped_helper_unit_peer(connection, runner=runner) as result:
        return result


def call_helper(socket_path, request):
    """No automatic retries: a lost start response must be queried by the same ID."""
    data = json.dumps(request).encode() + b"\n"
    if len(data) > MAX_REQUEST:
        raise HelperError("request too large")
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(150)
        connection.connect(str(socket_path))
        connection.sendall(data)
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = connection.recv(4096)
            if not chunk:
                raise HelperError("response lost; query the same operation ID")
            response.extend(chunk)
            if len(response) > 16384:
                raise HelperError("response too large")
    return json.loads(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for command in ("register", "recover-registration"):
        provision = modes.add_parser(command)
        for name in ("registry", "unit-dir", "root", "environment-file"):
            provision.add_argument("--" + name, type=Path, required=True)
        provision.add_argument("--instance-id", required=True)
        provision.add_argument("--username", required=True)
    deploy = modes.add_parser("deploy")
    for name in ("bootstrap-python", "registry", "unit-dir", "runtime-dir"):
        deploy.add_argument("--" + name, type=Path, required=True)
    deploy.add_argument("--group", type=int, required=True)
    deploy.add_argument("--candidate-version")
    recovery = modes.add_parser("recover-socket")
    recovery.add_argument("--registry", type=Path, required=True)
    recovery.add_argument("--socket", type=Path, required=True)
    daemon = modes.add_parser("serve")
    daemon.add_argument("--registry", type=Path, required=True)
    daemon.add_argument("--socket", type=Path, required=True)
    daemon.add_argument("--group", type=int, required=True)
    call = modes.add_parser("call")
    call.add_argument("--socket", type=Path, required=True)
    call.add_argument("--action", choices=sorted(ACTIONS), required=True)
    call.add_argument("--instance-id", required=True)
    call.add_argument("--operation-id", required=True)
    args = vars(parser.parse_args())
    mode = args.pop("mode")
    try:
        if mode in {"register", "recover-registration"}:
            print(
                json.dumps(register(**args, recover=mode == "recover-registration"), sort_keys=True)
            )
        elif mode == "deploy":
            print(json.dumps(deploy_helper(**args), sort_keys=True))
        elif mode == "recover-socket":
            print(json.dumps(recover_socket(args["registry"], args["socket"]), sort_keys=True))
        elif mode == "serve":
            serve(args["registry"], args["socket"], group=args["group"])
        else:
            request = {key: args[key] for key in ("action", "instance_id", "operation_id")}
            response = call_helper(args["socket"], request)
            print(json.dumps(response, sort_keys=True))
            if response.get("ok") is not True:
                parser.exit(1)
    except (HelperError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"helper refused: {exc}\n")


if __name__ == "__main__":
    main()
