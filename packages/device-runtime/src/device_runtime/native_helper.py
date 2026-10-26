"""Linux native service helper: administrator registration and bounded local IPC.

Only query/start/reconcile are implemented. No stop, arbitrary argv, installation,
package activation or automatic replay. Run the daemon via its dedicated root unit.
"""

import argparse
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
from contextlib import contextmanager
from pathlib import Path

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MAX_REQUEST = 4096
ACTIONS = {"query", "start", "reconcile"}


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


def _save_new(path, value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    # Hard-link publication provides no-replace semantics, even with other writers.
    fd, temporary = tempfile.mkstemp(prefix=".helper-", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
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


def render_helper_unit(*, bootstrap_python, registry, socket_path, group):
    python, registry, socket_path = map(_path, (bootstrap_python, registry, socket_path))
    if type(group) is not int or group < 0:
        raise HelperError("explicit socket group required")
    return (
        "[Unit]\nDescription=Device bounded native management helper\n"
        "After=local-fs.target\n\n[Service]\nType=simple\nUser=root\nGroup=root\n"
        f"ExecStart={python} -I -m device_runtime.native_helper serve "
        f"--registry {registry} --socket {socket_path} --group {group}\n"
        "UMask=0077\nNoNewPrivileges=yes\nRestart=no\n"
        "# No ExecStartPre unlink: an orphan socket requires explicit reconciliation.\n"
        "\n[Install]\nWantedBy=multi-user.target\n"
    )


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


def register(registry, unit_dir, *, instance_id, root, username, environment_file):
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
    if unit_path.exists() or record_path.exists():
        raise HelperError("registration already exists; explicit reconciliation required")
    content = render_client_unit(
        instance_id=instance_id,
        root=root,
        uid=account.pw_uid,
        gid=account.pw_gid,
        environment_file=environment_file,
    )
    # Record is published last. Orphan unit after interruption is never auto-adopted.
    descriptor = os.open(unit_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    record = {
        "schema_version": 1,
        "instance_id": instance_id,
        "root": str(root),
        "uid": account.pw_uid,
        "gid": account.pw_gid,
        "unit": unit_name,
        "unit_path": str(unit_path),
        "unit_sha256": _digest(unit_path),
        "runner_sha256": _digest(runner),
        "identity_sha256": _digest(root / "instance.json"),
        "roles_sha256": _digest(root / "config/roles.json"),
        "environment_file": str(environment_file),
    }
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
                "--no-pager",
                "--property=ActiveState,SubState,MainPID,User,Group,FragmentPath,ExecStart",
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        ).stdout
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    def start(self, unit):
        subprocess.run(
            [self.executable, "start", unit],
            check=True,
            capture_output=True,
            timeout=120,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )


class Helper:
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
        return {k: snapshot.get(k) for k in ("ActiveState", "SubState", "MainPID")}

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
            if action != "start":
                path = self.registry / f"op-{instance_id}-{operation_id}.json"
                result["intent"] = json.loads(path.read_text()) if path.exists() else None
                return result
            intent_path = self.registry / f"op-{instance_id}-{operation_id}.json"
            result_path = self.registry / f"result-{instance_id}-{operation_id}.json"
            intent = {
                "action": "start",
                "instance_id": instance_id,
                "operation_id": operation_id,
                "unit_sha256": record["unit_sha256"],
            }
            if intent_path.exists():
                if json.loads(intent_path.read_text()) != intent:
                    raise HelperError("operation identity conflict")
                return {
                    **result,
                    "result": json.loads(result_path.read_text())
                    if result_path.exists()
                    else {"state": "unknown"},
                    "replayed": False,
                }
            for pending in self.registry.glob(f"op-{instance_id}-*.json"):
                result_file = pending.with_name(pending.name.replace("op-", "result-", 1))
                if (
                    not result_file.exists()
                    or json.loads(result_file.read_text()).get("state") == "unknown"
                ):
                    raise HelperError("unconfirmed operation blocks a new start; reconcile first")
            if snapshot["ActiveState"] not in {"inactive", "failed", "active"}:
                raise HelperError("service transition requires read-only reconciliation")
            _save_new(intent_path, intent)
            outcome = {"state": "already_active"}
            if snapshot["ActiveState"] != "active":
                try:
                    with _instance_service_lease(Path(record["root"])):
                        self.adapter.start(record["unit"])
                        observation = self._snapshot(record)
                    outcome = {
                        "state": "started" if observation["ActiveState"] == "active" else "unknown",
                        "observation": observation,
                    }
                except (OSError, subprocess.SubprocessError, HelperError):
                    outcome = {"state": "unknown"}
            _save_new(result_path, outcome)
            return {**result, "result": outcome, "replayed": False}


def serve(registry, socket_path, *, group):
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
                        chunk = connection.recv(min(1024, MAX_REQUEST + 1 - len(data)))
                        if not chunk:
                            raise HelperError("incomplete request")
                        data.extend(chunk)
                        if len(data) > MAX_REQUEST:
                            raise HelperError("request too large")
                    result = helper.handle(json.loads(data), peer_uid=uid)
                    response = {"ok": True, "result": result}
                except (HelperError, ValueError, KeyError, OSError, subprocess.SubprocessError):
                    # Never return command stderr or environment/credential content.
                    response = {"ok": False, "error": "helper_request_refused"}
                try:
                    connection.sendall(json.dumps(response).encode() + b"\n")
                except OSError:
                    pass  # A lost response must not cause the action to replay.


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
    provision = modes.add_parser("register")
    for name in ("registry", "unit-dir", "root", "environment-file"):
        provision.add_argument("--" + name, type=Path, required=True)
    provision.add_argument("--instance-id", required=True)
    provision.add_argument("--username", required=True)
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
        if mode == "register":
            print(json.dumps(register(**args), sort_keys=True))
        elif mode == "serve":
            serve(args["registry"], args["socket"], group=args["group"])
        else:
            request = {key: args[key] for key in ("action", "instance_id", "operation_id")}
            print(json.dumps(call_helper(args["socket"], request), sort_keys=True))
    except (HelperError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"helper refused: {exc}\n")


if __name__ == "__main__":
    main()
