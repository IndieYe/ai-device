"""Independent transaction runtime snapshots; no package/service execution.

The caller must hold the package transaction lease. Snapshots are deliberately
never cleaned or repaired here. An incomplete directory remains blocked.
"""

import array
import ctypes
import fcntl
import hashlib
import json
import math
import os
import re
import select
import shutil
import signal
import socket
import stat
import struct
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import native_helper as helper


class BootstrapError(ValueError):
    pass


# This versioned bootstrap is installed separately from candidate snapshots.
# Its package provenance and lifecycle still require independent verification.
TRUSTED_HELPER_BOOTSTRAP = Path("/opt/ai-device/bootstrap/0.2.2-native1/runtime")


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory(root):
    entries = {}
    total = 0
    for base, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(base) / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if len(entries) >= 50000:
                raise BootstrapError("runtime member budget exceeded")
            if not stat.S_ISLNK(info.st_mode):
                helper._trusted(path, directory=stat.S_ISDIR(info.st_mode))
            else:
                helper._trusted(path.parent, directory=True)
                if info.st_uid != root.stat().st_uid:
                    raise BootstrapError("runtime link owner differs")
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if os.path.isabs(target) or not path.resolve().is_relative_to(root):
                    raise BootstrapError("runtime link must remain inside copied tree")
                if not path.exists():
                    raise BootstrapError("runtime link target unavailable")
                value = {"kind": "symlink", "target": target}
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > 2_000_000_000:
                    raise BootstrapError("runtime byte budget exceeded")
                value = {
                    "kind": "file",
                    "mode": stat.S_IMODE(info.st_mode),
                    "size": info.st_size,
                    "sha256": _digest(path),
                }
            elif stat.S_ISDIR(info.st_mode):
                value = {"kind": "directory", "mode": stat.S_IMODE(info.st_mode)}
            else:
                raise BootstrapError("unsupported runtime member")
            entries[relative] = value
    return entries


def validate_snapshot(directory):
    directory = helper._path(directory)
    helper._trusted(directory, directory=True)
    receipt_path = directory / "snapshot.json"
    helper._trusted(receipt_path)
    receipt = json.loads(receipt_path.read_bytes())
    runtime = directory / "runtime"
    helper._trusted(runtime, directory=True)
    if type(receipt.get("schema_version")) is not int or receipt.get("schema_version") != 1 or receipt.get("state") != "runtime_snapshot":
        raise BootstrapError("invalid runtime snapshot receipt")
    if not helper._same_json(_inventory(runtime), receipt["inventory"]):
        raise BootstrapError("runtime snapshot drift")
    python = runtime / "bin/python3.13"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise BootstrapError("snapshot interpreter unavailable")
    return receipt


def snapshot_runtime(source, directory):
    """Copy a trusted CPython tree outside package ownership, never hardlink.

    The destination must be beneath the manager's separately owned transaction
    directory, not within the source runtime. This function does not infer or
    grant package authorization. Reusing a completed snapshot verifies bytes;
    missing receipt never triggers recopy/overwrite.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    source, directory = helper._path(source), helper._path(directory)
    helper._trusted(source, directory=True)
    helper._trusted(directory.parent, directory=True)
    if directory.is_relative_to(source) or source.is_relative_to(directory):
        raise BootstrapError("snapshot must be independent of source tree")
    if directory.exists() or directory.is_symlink():
        receipt = validate_snapshot(directory)
        if receipt["source"] != str(source):
            raise BootstrapError("snapshot source conflict")
        return receipt
    before = _inventory(source)
    python = source / "bin/python3.13"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise BootstrapError("source interpreter unavailable")
    directory.mkdir(mode=0o700)
    with helper._directory_fd(directory.parent) as fd:
        os.fsync(fd)
    copied = directory / "runtime"
    shutil.copytree(source, copied, symlinks=True, copy_function=shutil.copyfile)
    # copyfile does not preserve executable bits; restore exact validated modes.
    for relative, value in before.items():
        if value["kind"] != "symlink":
            (copied / relative).chmod(value["mode"])
    copied.chmod(stat.S_IMODE(source.stat().st_mode))
    if _inventory(source) != before or _inventory(copied) != before:
        raise BootstrapError("runtime changed while copying")
    for relative, value in before.items():
        if value["kind"] == "file":
            with (copied / relative).open("rb") as stream:
                os.fsync(stream.fileno())
    directories = [copied / name for name, value in before.items() if value["kind"] == "directory"]
    for path in sorted(directories, key=lambda p: len(p.parts), reverse=True) + [copied]:
        with helper._directory_fd(path) as fd:
            os.fsync(fd)
    receipt = {
        "schema_version": 1,
        "state": "runtime_snapshot",
        "source": str(source),
        "inventory": before,
    }
    helper._save_new(directory / "snapshot.json", receipt)
    return validate_snapshot(directory)


def _reject_helper_bytecode(snapshot):
    """A source digest cannot identify code loaded from a stale bytecode cache.

    This is only a candidate precondition, not proof of a running process's
    loaded code. Do not delete or rewrite any existing cache automatically.
    """
    inventory = snapshot.get("inventory")
    if not isinstance(inventory, dict):
        raise BootstrapError("invalid runtime inventory")
    if any(
        part == "__pycache__" or part.endswith((".pyc", ".pyo"))
        for relative in inventory
        for part in relative.split("/")
    ):
        raise BootstrapError("helper candidate contains bytecode cache")


def inspect_helper_candidate(directory):
    """Validate an independent snapshot and probe its helper without activating it.

    Only administrator-trusted candidate code is executed. This is not a
    signature check, a service switch, or authorization to replace a bootstrap.
    """
    import subprocess

    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    directory = helper._path(directory)
    snapshot = validate_snapshot(directory)
    _reject_helper_bytecode(snapshot)
    runtime = directory / "runtime"
    python = runtime / "bin/python3.13"
    expected = runtime / "lib/python3.13/site-packages/device_runtime/native_helper.py"
    helper._trusted(expected)
    code = (
        "import json,sys; import device_runtime.native_helper as h; "
        "print(json.dumps({'base':sys.base_prefix,'module':h.__file__,"
        "'version':list(sys.version_info[:2])}))"
    )
    try:
        result = subprocess.run(
            [str(python), "-I", "-B", "-c", code],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
        observed = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise BootstrapError("helper candidate self-check failed") from exc
    if observed != {"base": str(runtime), "module": str(expected), "version": [3, 13]}:
        raise BootstrapError("helper candidate identity differs")
    # Detect modifications during the subprocess before reporting usable evidence.
    validate_snapshot(directory)
    return {
        "state": "helper_candidate_checked_not_activated",
        "snapshot_sha256": _digest(directory / "snapshot.json"),
        "helper_sha256": _digest(expected),
        "python": str(python),
        "activated": False,
    }


def publish_helper_candidate(source, candidates_dir, version):
    """Prepare a versioned, independently checked candidate without activating it.

    The caller must serialize publication with the administrator package lease.
    A partially prepared version is never removed or overwritten.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    helper._identifier(version)
    source = helper._path(source)
    candidates_dir = helper._path(candidates_dir)
    helper._trusted(candidates_dir, directory=True)
    candidate = candidates_dir / version
    # An existing complete snapshot can be revalidated; incomplete data is
    # rejected by snapshot_runtime and retained for explicit review.
    snapshot_runtime(source, candidate)
    checked = inspect_helper_candidate(candidate)
    receipt = {
        "schema_version": 1,
        "state": "helper_candidate_published_not_activated",
        "version": version,
        "source": str(source),
        "snapshot_sha256": checked["snapshot_sha256"],
        "helper_sha256": checked["helper_sha256"],
        "activated": False,
    }
    path = candidate / "candidate.json"
    if os.path.lexists(path):
        helper._trusted(path)
        if not helper._same_json(json.loads(path.read_bytes()), receipt):
            raise BootstrapError("candidate publication evidence differs")
    else:
        helper._save_new(path, receipt)
    return receipt


def read_helper_candidate(candidates_dir, version):
    """Verify published candidate evidence without executing or activating code."""
    helper._identifier(version)
    candidates_dir = helper._path(candidates_dir)
    helper._trusted(candidates_dir, directory=True)
    candidate = candidates_dir / version
    snapshot = validate_snapshot(candidate)
    _reject_helper_bytecode(snapshot)
    path = candidate / "candidate.json"
    helper._trusted(path)
    value = json.loads(path.read_bytes())
    expected = candidate / "runtime/lib/python3.13/site-packages/device_runtime/native_helper.py"
    helper._trusted(expected)
    evidence = {
        "schema_version": 1,
        "state": "helper_candidate_published_not_activated",
        "version": version,
        "source": snapshot["source"],
        "snapshot_sha256": _digest(candidate / "snapshot.json"),
        "helper_sha256": _digest(expected),
        "activated": False,
    }
    if not helper._same_json(value, evidence):
        raise BootstrapError("candidate publication evidence differs")
    return evidence


def run_verified_helper_candidate(candidates_dir, version, *, registry, socket_path, group, expected_snapshot_sha256=None):
    """Execute verified candidate helper bytes in the independent bootstrap.

    This experimental entry point does not provision a unit, prove the caller's
    identity, or authorize dispatch. The caller is trusted administrator code.
    It must run under the separately installed isolated interpreter with bytecode
    writes disabled. Modules are compiled from the exact bytes just checked,
    not imported again from mutable paths.
    """
    contents = _verified_helper_bytes(candidates_dir, version, expected_snapshot_sha256)
    with _verified_helper_modules(contents) as candidate:
        candidate.serve(registry, socket_path, group=group)


@contextmanager
def _verified_helper_modules(contents):
    """Temporarily load caller-verified bytes; not an identity or authorization proof.

    The caller is responsible for obtaining and retaining validated bytes. This
    process-global module replacement requires single-threaded exclusive use.
    """
    import types

    names = ("__init__", "management_lock", "native_helper")
    # Keep imports for these modules inside this exact validated byte set.
    module_names = ("device_runtime", "device_runtime.management_lock", "device_runtime.native_helper")
    previous = {name: sys.modules.get(name) for name in module_names}
    try:
        for name, qualified in zip(names, module_names, strict=True):
            path, data = contents[name]
            module = types.ModuleType(qualified)
            module.__file__ = str(path)
            module.__package__ = "device_runtime"
            if name == "__init__":
                module.__path__ = [str(path.parent)]
            sys.modules[qualified] = module
            exec(compile(data, str(path), "exec"), module.__dict__)  # noqa: S102
        yield sys.modules["device_runtime.native_helper"]
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _verified_helper_bytes(candidates_dir, version, expected_snapshot_sha256):
    """Validate the independent bootstrap and return its verified source bytes.

    Internal preparation only; returning bytes does not attest a running process
    or authorize dispatch. Callers must preserve the validated execution boundary.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    candidate = helper._path(candidates_dir) / version
    runtime = candidate / "runtime"
    bootstrap = TRUSTED_HELPER_BOOTSTRAP
    python = bootstrap / "bin/python3.13"
    site = bootstrap / "lib/python3.13/site-packages/device_runtime"
    if (
        Path(sys.executable) != python
        or Path(sys.base_prefix) != bootstrap
        or Path(__file__) != site / "native_bootstrap.py"
        or Path(helper.__file__) != site / "native_helper.py"
        or candidate.is_relative_to(bootstrap)
        or bootstrap.is_relative_to(candidate)
        or not sys.flags.isolated
        or not sys.dont_write_bytecode
    ):
        raise BootstrapError("independent isolated bootstrap required")
    for path in (bootstrap, python, site / "native_bootstrap.py", site / "native_helper.py", site / "management_lock.py"):
        helper._trusted(path, directory=path == bootstrap)
    evidence = read_helper_candidate(candidates_dir, version)
    if expected_snapshot_sha256 is not None and (
        not isinstance(expected_snapshot_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_snapshot_sha256)
        or evidence["snapshot_sha256"] != expected_snapshot_sha256
    ):
        raise BootstrapError("candidate snapshot differs from unit")
    snapshot = validate_snapshot(candidate)
    if _digest(candidate / "snapshot.json") != evidence["snapshot_sha256"]:
        raise BootstrapError("candidate snapshot changed")
    names = ("__init__", "management_lock", "native_helper")
    contents = {}
    for name in names:
        relative = f"lib/python3.13/site-packages/device_runtime/{name}.py"
        entry = snapshot["inventory"].get(relative)
        if not isinstance(entry, dict) or entry.get("kind") != "file":
            raise BootstrapError("candidate helper source missing")
        path = runtime / relative
        helper._trusted(path)
        data = path.read_bytes()
        if len(data) != entry.get("size") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            raise BootstrapError("candidate helper source changed")
        contents[name] = (path, data)
    if hashlib.sha256(contents["native_helper"][1]).hexdigest() != evidence["helper_sha256"]:
        raise BootstrapError("candidate helper evidence differs")
    if validate_snapshot(candidate) != snapshot or _digest(candidate / "snapshot.json") != evidence["snapshot_sha256"]:
        raise BootstrapError("candidate changed during launch")

    return contents


def publish_managed_helper_candidate(registry, source, version):
    """Publish under the fixed manager directory while holding the package lease."""
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    registry = helper._path(registry)
    helper._trusted(registry, directory=True)
    candidates = registry / "bootstrap-candidates"
    # Existing administrator-provisioned directory only; no implicit setup.
    helper._trusted(candidates, directory=True)
    with helper.package_lease(registry, exclusive=True):
        result = publish_helper_candidate(source, candidates, version)
        if read_helper_candidate(candidates, version) != result:
            raise BootstrapError("candidate publication verification differs")
        return result


def inspect_helper_transition(registry, unit_dir, version):
    """Read-only evidence for review, not permission to replace or restart a helper."""
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("Linux administrator required")
    registry, unit_dir = map(helper._path, (registry, unit_dir))
    helper._trusted(registry, directory=True)
    helper._trusted(unit_dir, directory=True)
    candidates = registry / "bootstrap-candidates"
    unit = unit_dir / "ai-device-native-helper.service"
    with helper.package_lease(registry, exclusive=True, existing=True):
        helper._trusted(unit)
        before = _digest(unit)
        candidate = read_helper_candidate(candidates, version)
        candidate_path = candidates / version / "candidate.json"
        candidate_digest = _digest(candidate_path)
        # Do not infer process state or protocol compatibility from unit content.
        if _digest(unit) != before:
            raise BootstrapError("helper unit changed during observation")
        return {
            "state": "helper_transition_observed_not_authorized",
            "unit": str(unit),
            "unit_sha256": before,
            "candidate_version": version,
            "candidate_sha256": candidate_digest,
            "snapshot_sha256": candidate["snapshot_sha256"],
            "process_quiescence_verified": False,
            "protocol_compatibility_verified": False,
            "switch_allowed": False,
        }


def _handshake_pidfd_alive(pidfd):
    """Observe a pidfd now; this never reserves process lifetime."""
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
    return not poller.poll(0)


def _reap_direct_child_pidfd(pidfd, *, deadline):
    """Kill and reap one direct child, without signaling a reusable numeric PID.

    Caller owns the pidfd, prevents other waiters from reaping this child, and
    holds required leases until this succeeds. This cannot reap descendants,
    prove a process tree quiescent, or authorize recovery or a new action.
    """
    if type(pidfd) is not int or pidfd < 0 or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise BootstrapError("invalid child reap inputs")
    if sys.platform != "linux" or not all(hasattr(os, name) for name in ("P_PIDFD", "WNOWAIT", "waitid")) or not hasattr(signal, "pidfd_send_signal"):
        raise BootstrapError("direct child pidfd reap unavailable")
    try:
        if time.monotonic() >= deadline:
            raise BootstrapError("child reap deadline exceeded")
        observed = os.waitid(os.P_PIDFD, pidfd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if observed is None:
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The direct child may have exited between observation and signal.
        while True:
            result = os.waitid(os.P_PIDFD, pidfd, os.WEXITED | os.WNOHANG)
            if result is not None:
                return result.si_pid, result.si_code, result.si_status
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BootstrapError("child reap deadline exceeded")
            time.sleep(min(remaining, 0.01))
    except ChildProcessError as exc:
        raise BootstrapError("pidfd is not an unreaped direct child") from exc
    except OSError as exc:
        raise BootstrapError("direct child pidfd reap failed") from exc


def _hold_until_direct_child_reaped(attempt):
    """Keep the caller's leases until an attempt confirms direct-child reaping.

    Fail closed indefinitely on unknown results, including ECHILD; a different
    waiter is not proof that this supervisor reaped its child. SIGKILL, process
    or host failure can still release leases. This is not a tree/job fence.
    """
    unknown = False
    while True:
        try:
            attempt()
        except BaseException:  # noqa: BLE001 - an unknown result must not release leases
            if not unknown:
                unknown = True
                try:
                    os.write(2, b"query supervisor: direct child reap unknown; holding leases and retrying\n")
                except BaseException:  # noqa: BLE001, S110 - diagnostics cannot release leases
                    pass
            try:
                time.sleep(1)
            except BaseException:  # noqa: BLE001, S112 - interruption is not a reap receipt
                continue
        else:
            if unknown:
                raise BootstrapError("direct child reaped after unknown cleanup; not authorized")
            return


def _reap_gated_child_waitpid(pid, *, deadline):
    """Reap the still-gated direct child without signaling a numeric PID."""
    while True:
        waited, _ = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return
        if time.monotonic() >= deadline:
            raise BootstrapError("gated child cleanup unknown")
        time.sleep(0.01)


def _publish_query_supervisor_intent(registry, *, run_id, snapshot_sha256):
    """Publish a one-use candidate intent; not a cross-entry fence.

    The caller must hold all participating leases and must not start any
    subprocess before successful return. Existing or uncertain publication is
    not reusable. No automatic retirement or recovery is implemented.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise BootstrapError("invalid query supervisor run id")
    if not isinstance(snapshot_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot_sha256):
        raise BootstrapError("invalid query supervisor snapshot digest")
    registry = helper._path(registry)
    helper._trusted(registry, directory=True)
    intent = registry / "query-supervisor.intent.json"
    if os.path.lexists(intent):
        raise BootstrapError("query supervisor intent already exists; remains unknown")
    value = {
        "schema_version": 1,
        "state": "query_supervisor_intent",
        "run_id": run_id,
        "snapshot_sha256": snapshot_sha256,
        "protocol": "query-supervisor-intent-v1",
    }
    try:
        helper._save_new(intent, value)
    except (OSError, ValueError) as exc:
        raise BootstrapError("query supervisor intent publication failed or unknown") from exc
    return value


@contextmanager
def _exclusive_query_subreaper():
    """Dedicated process only: no existing children or competing child waiters."""
    try:
        os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        pass
    else:
        raise BootstrapError("query supervisor requires no existing children")
    libc = ctypes.CDLL(None, use_errno=True)
    prior = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(prior), 0, 0, 0) != 0 or prior.value != 0:
        raise BootstrapError("query supervisor requires inactive subreaper")
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise BootstrapError("query subreaper unavailable")
    try:
        enabled = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(enabled), 0, 0, 0) != 0 or enabled.value != 1:
            raise BootstrapError("query subreaper verification failed")
        yield
    finally:
        if libc.prctl(36, 0, 0, 0, 0) != 0:
            raise BootstrapError("query subreaper reset failed")


def _wait_adopted_query_children():
    """Reap adopted children until ECHILD; never signal a reusable PID.

    Only a dedicated single-threaded subreaper with exclusive wait ownership
    may interpret ECHILD. This cannot observe external delegated work.
    """
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            time.sleep(0.1)


def _observe_child_handshake(connection, pidfd, child_pid, challenge, *, deadline):
    """Observe a direct child record on a caller-owned private SEQPACKET channel.

    The caller must create the channel, enable SO_PASSCRED before data, fork a
    single direct child, obtain its pidfd before releasing the child, and keep
    both child and descriptors under its own lifecycle control. This function
    cannot prove those preconditions or pidfd-to-PID correspondence. A matching
    record is not a code, invocation, handler, or dispatch authorization.
    """
    if sys.platform != "linux" or not all(
        hasattr(socket, name) for name in ("SO_PASSCRED", "SCM_CREDENTIALS", "SCM_RIGHTS")
    ):
        raise BootstrapError("child credential handshake unavailable")
    if type(child_pid) is not int or child_pid <= 0 or not isinstance(challenge, bytes) or len(challenge) != 32:
        raise BootstrapError("invalid child handshake inputs")
    try:
        if (
            connection.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_SEQPACKET
            or connection.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) != 1
        ):
            raise BootstrapError("unprepared child handshake channel")
        if not _handshake_pidfd_alive(pidfd):
            raise BootstrapError("child exited before handshake")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BootstrapError("child handshake deadline exceeded")
        connection.settimeout(remaining)
        size = struct.calcsize("3i")
        data, controls, flags, _ = connection.recvmsg(
            65, socket.CMSG_SPACE(size) + socket.CMSG_SPACE(256), getattr(socket, "MSG_CMSG_CLOEXEC", 0)
        )
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
            raise BootstrapError("cannot close unexpected handshake descriptors")
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(controls) != 1:
            raise BootstrapError("invalid child handshake record")
        level, kind, payload = controls[0]
        if level != socket.SOL_SOCKET or kind != socket.SCM_CREDENTIALS or len(payload) != size:
            raise BootstrapError("invalid child handshake credential")
        pid, uid, _gid = struct.unpack("3i", payload)
        if pid != child_pid or uid != 0 or data != b"READY:" + challenge:
            raise BootstrapError("child handshake differs")
        if time.monotonic() >= deadline:
            raise BootstrapError("child handshake deadline exceeded")
        if not _handshake_pidfd_alive(pidfd):
            raise BootstrapError("child exited during handshake")
        return "observed_not_authorized"
    except OSError as exc:
        raise BootstrapError("child handshake failed") from exc


def _gated_candidate_query_child(control, contents, registry, socket_path, *, group, deadline):
    """Run query-only candidate after a private gate; not a production entry.

    Caller must be a controlled direct child inheriting already validated bytes
    and both daemon leases. This does not verify parent identity or continued
    supervision, and must not be used to authorize any operation.
    """
    def set_remaining():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BootstrapError("child gate deadline exceeded")
        control.settimeout(remaining)

    try:
        set_remaining()
        token = control.recv(33)
        if len(token) != 32:
            raise BootstrapError("invalid child gate challenge")
        set_remaining()
        control.sendall(b"READY:" + token)
        set_remaining()
        if control.recv(3) != b"GO" or time.monotonic() >= deadline:
            raise BootstrapError("child gate not released")
    except OSError as exc:
        raise BootstrapError("child gate communication failed") from exc
    finally:
        control.close()
    with _verified_helper_modules(contents) as candidate:
        candidate._serve_credential_queries_locked(registry, socket_path, group=group)


def _start_parent_exit_watchdog(parent_pidfd):
    """Exit this query-only child after parent death; not an action fence.

    The caller owns a pidfd for the actual parent obtained before fork. This
    observes eventual parent death, not atomic authorization, descendant exit,
    or completion of external work. The child retains the fd until it exits.
    """
    if not _handshake_pidfd_alive(parent_pidfd):
        raise BootstrapError("supervisor already exited")

    def watch():
        try:
            poller = select.poll()
            poller.register(parent_pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
            poller.poll()
        finally:
            os._exit(1)

    threading.Thread(target=watch, daemon=True, name="parent-pidfd-watchdog").start()


def _supervise_candidate_queries(candidates_dir, version, *, registry, socket_path, group, expected_snapshot_sha256):
    """Unwired query-only supervisor candidate; no operation authorization.

    Requires a dedicated single-threaded Linux administrator process. The
    registry stability precondition remains mandatory. Parent death is observed
    asynchronously by the child; escaped descendants, external jobs and action
    fencing are NOT handled or proven.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BootstrapError("query supervisor requires Linux administrator identity")
    if not isinstance(expected_snapshot_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_snapshot_sha256):
        raise BootstrapError("fixed candidate snapshot digest required")
    if not all(hasattr(os, name) for name in ("fork", "pidfd_open", "P_PIDFD", "WNOWAIT")) or not all(
        hasattr(signal, name) for name in ("pthread_sigmask", "sigtimedwait", "pidfd_send_signal")
    ):
        raise BootstrapError("query supervision unavailable")
    if len(list(Path("/proc/self/task").iterdir())) != 1:
        raise BootstrapError("query supervisor requires one thread")
    if (
        signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL
        or signal.getsignal(signal.SIGTERM) != signal.SIG_DFL
        or signal.getsignal(signal.SIGINT) not in (signal.SIG_DFL, signal.default_int_handler)
    ):
        raise BootstrapError("query supervisor requires default signal handlers")
    stops = {signal.SIGTERM, signal.SIGINT}
    oldmask = signal.pthread_sigmask(signal.SIG_BLOCK, stops)
    try:
        if stops & oldmask or stops & signal.sigpending():
            raise BootstrapError("query supervisor stop signal already pending or blocked")
        # Publication needs EX: a concurrent start checks intent under SH.
        # Lock conversion need not be atomic: the published intent fences all
        # participating readers even if another process acquires in the gap.
        with (
            helper._daemon_lease_pair(registry),
            helper.package_lease(registry, exclusive=True) as package_fd,
            _exclusive_query_subreaper(),
        ):
            # Intentionally never retired here: this private candidate cannot
            # establish a durable terminal state or authorize another run.
            _publish_query_supervisor_intent(
                registry, run_id=os.urandom(16).hex(), snapshot_sha256=expected_snapshot_sha256
            )
            fcntl.flock(package_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            try:
                contents = _verified_helper_bytes(candidates_dir, version, expected_snapshot_sha256)
            finally:
                # Candidate import runs in a subprocess and can itself leave
                # descendants. They must be reaped before any handler fork.
                _hold_until_direct_child_reaped(_wait_adopted_query_children)
            parent, child_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            pid = None
            pidfd = None
            self_pidfd = None
            try:
                parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                if stops & signal.sigpending():
                    raise BootstrapError("query supervisor stopped before fork")
                self_pidfd = os.pidfd_open(os.getpid())
                pid = os.fork()
                if pid == 0:
                    try:
                        parent.close()
                        signal.pthread_sigmask(signal.SIG_SETMASK, oldmask)
                        _start_parent_exit_watchdog(self_pidfd)
                        _gated_candidate_query_child(
                            child_socket, contents, registry, socket_path, group=group,
                            deadline=time.monotonic() + 5,
                        )
                    except BaseException:  # noqa: BLE001 - child must never return into parent control flow
                        os._exit(1)
                    os._exit(0)
                child_socket.close()
                pidfd = os.pidfd_open(pid)
                challenge = os.urandom(32)
                parent.sendall(challenge)
                _observe_child_handshake(parent, pidfd, pid, challenge, deadline=time.monotonic() + 5)
                if stops & signal.sigpending():
                    raise BootstrapError("query supervisor stopped before release")
                parent.sendall(b"GO")
                parent.close()
                while _handshake_pidfd_alive(pidfd):
                    if signal.sigtimedwait(stops, 0.1) is not None:
                        return "query_supervisor_stopped_not_authorized"
                raise BootstrapError("query child exited unexpectedly")
            finally:
                try:
                    try:
                        parent.close()
                    finally:
                        child_socket.close()
                finally:
                    try:
                        if self_pidfd is not None:
                            os.close(self_pidfd)
                    finally:
                        if pid is not None:
                            try:
                                if pidfd is None:
                                    _hold_until_direct_child_reaped(
                                        lambda: _reap_gated_child_waitpid(pid, deadline=time.monotonic() + 5)
                                    )
                                else:
                                    try:
                                        _hold_until_direct_child_reaped(
                                            lambda: _reap_direct_child_pidfd(pidfd, deadline=time.monotonic() + 5)
                                        )
                                    finally:
                                        os.close(pidfd)
                            finally:
                                _hold_until_direct_child_reaped(
                                    _wait_adopted_query_children
                                )
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, oldmask)


def main():
    """Explicit experimental candidate daemon entry; no activation or dispatch."""
    import argparse

    parser = argparse.ArgumentParser(description="Run a verified helper candidate (experimental)")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--group", type=int, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--snapshot-sha256")
    args = parser.parse_args()
    try:
        registry = helper._path(args.registry)
        run_verified_helper_candidate(
            registry / "bootstrap-candidates", args.version,
            registry=registry, socket_path=args.socket, group=args.group,
            expected_snapshot_sha256=args.snapshot_sha256,
        )
    except (BootstrapError, helper.HelperError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"candidate helper refused: {exc}\n")


if __name__ == "__main__":
    main()
