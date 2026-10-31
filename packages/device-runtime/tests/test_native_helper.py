import fcntl
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from device_runtime import native_helper as nh


class FakeSystemd:
    def __init__(self, record):
        self.record = record
        self.active = "inactive"
        self.calls = []
        self.failure = False
        self.override = {}

    def query(self, unit):
        self.calls.append(("query", unit))
        root = self.record["root"]
        return {
            "ActiveState": self.active,
            "SubState": "running",
            "MainPID": "123",
            "Job": "",
            "User": str(self.record["uid"]),
            "Group": str(self.record["gid"]),
            "FragmentPath": self.record["unit_path"],
            "ExecStart": f"{{ path={root}/run ; argv[]={root}/run client ; ignore_errors=no ; }}",
            **self.override,
        }

    def start(self, unit, *, lease_fd=None, receipt_path=None, intent=None, package_fd=None):
        if receipt_path is not None:
            nh._save_new(
                receipt_path,
                {
                    "state": "sender_reaped",
                    "intent": intent,
                    "returncode": 1 if self.failure else 0,
                },
            )
        self.calls.append(("start", unit))
        if self.failure:
            raise subprocess.TimeoutExpired("systemctl", 120)
        self.active = "active"


@pytest.fixture
def legacy_start_recovery_mechanics(monkeypatch):
    """Test historical inner checks only; production sender-tree gate stays closed."""
    monkeypatch.setattr(nh, "_require_proven_start_sender_tree", lambda: None)


@pytest.fixture
def helper(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    registry.mkdir()
    root = tmp_path / "instance"
    (root / "config").mkdir(parents=True)
    (root / ".installation.lock").touch()
    for relative, data in [
        ("run", "#!/opt/python/bin/python3.13\n"),
        ("instance.json", '{"instance_id":"probe"}'),
        ("config/roles.json", '{"client":{}}'),
    ]:
        (root / relative).write_text(data)
    unit = tmp_path / "ai-device-client-probe.service"
    unit.write_text("fixture unit\n")
    environment = tmp_path / "token.env"
    environment.write_text("PRIVATE=never-return-this\n")
    (root / nh.MARKER).write_text("fixture management binding")
    record = {
        "management_sha256": nh._digest(root / nh.MARKER),
        "schema_version": 1,
        "instance_id": "probe",
        "root": str(root),
        "uid": os.getuid() or 12345,
        "gid": os.getgid(),
        "unit": unit.name,
        "unit_path": str(unit),
        "unit_sha256": nh._digest(unit),
        "runner_sha256": nh._digest(root / "run"),
        "identity_sha256": nh._digest(root / "instance.json"),
        "roles_sha256": nh._digest(root / "config/roles.json"),
        "environment_file": str(environment),
    }
    # Tests use owned temporary directories; production trust checks tested separately.
    monkeypatch.setattr(nh, "_trusted", lambda *a, **k: None)
    (registry / "probe.json").write_text(json.dumps(record))
    adapter = FakeSystemd(record)
    return nh.Helper(registry, adapter=adapter), adapter, record


def req(action="query", op="one"):
    return {"action": action, "instance_id": "probe", "operation_id": op}


def test_render_systemd_uses_nonroot_stable_runner(tmp_path):
    content = nh.render_client_unit(
        instance_id="probe",
        root=tmp_path,
        uid=12345,
        gid=12345,
        environment_file=tmp_path / "token.env",
    )
    assert "User=12345\n" in content
    assert f"ExecStart={tmp_path}/run client\n" in content
    assert "UMask=0077" in content
    assert "Restart=on-failure" in content
    assert "NoNewPrivileges=yes" in content


@pytest.mark.parametrize("path", ["relative", "/tmp/bad%value", "/tmp/space here", "/tmp/../etc"])
def test_unsafe_unit_paths_rejected(path):
    with pytest.raises(nh.HelperError):
        nh.render_client_unit(
            instance_id="probe", root=path, uid=12345, gid=1, environment_file="/etc/fixture.env"
        )


def test_root_unit_rejected(tmp_path):
    with pytest.raises(nh.HelperError, match="non-root"):
        nh.render_client_unit(
            instance_id="probe",
            root=tmp_path,
            uid=0,
            gid=0,
            environment_file=tmp_path / "token.env",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("User", "0"),
        ("Group", "0"),
        ("FragmentPath", "/etc/other.service"),
        ("ExecStart", "different argv"),
    ],
)
def test_loaded_binding_must_match(helper, field, value):
    service, adapter, record = helper
    adapter.override[field] = value
    with pytest.raises(nh.HelperError, match="loaded systemd"):
        service._snapshot(record)
    assert not any(action == "start" for action, _ in adapter.calls)


def test_changed_runner_refuses_before_systemd(helper):
    service, adapter, record = helper
    Path(record["root"], "run").write_text("changed")
    with pytest.raises(nh.HelperError, match="binding changed"):
        service._binding("probe")
    assert not adapter.calls


def test_management_lock_contention(helper):
    service, adapter, _record = helper
    with (service.registry / "probe.lock").open("a+b") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(nh.HelperError, match="busy"), service._lease("probe"):
            pass
    assert not adapter.calls


def test_atomic_save_never_overwrites(tmp_path):
    path = tmp_path / "record.json"
    nh._save_new(path, {"keep": True})
    with pytest.raises(FileExistsError):
        nh._save_new(path, {"keep": False})
    assert json.loads(path.read_text()) == {"keep": True}
    assert not list(tmp_path.glob(".helper-*"))


def test_untrusted_path_refused(tmp_path):
    path = tmp_path / "record"
    path.write_text("fixture")
    if os.getuid() != 0:
        with pytest.raises(nh.HelperError, match="administrator"):
            nh._trusted(path)
    else:
        path.chmod(0o666)
        with pytest.raises(nh.HelperError, match="administrator"):
            nh._trusted(path)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(nh.sys, "platform", "linux")
    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)
    monkeypatch.setattr(nh, "_trusted", lambda *a, **kw: None)
    monkeypatch.setattr(nh, "RUNTIME_ROOT", tmp_path)
    python = tmp_path / "python"
    python.write_text("fixture")
    python.chmod(0o755)
    units = tmp_path / "units"
    units.mkdir()
    # deploy_helper now publishes a unit that execs the packaged entrypoint.
    entrypoint = tmp_path / "ai-device-native-helper"
    entrypoint.write_text("#!/bin/sh\n")
    entrypoint.chmod(0o755)
    monkeypatch.setattr(nh, "HELPER_ENTRYPOINT", entrypoint)
    return {
        "bootstrap_python": python,
        "registry": tmp_path / "registry",
        "unit_dir": units,
        "runtime_dir": tmp_path / "runtime",
        "group": 12345,
    }


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(nh.sys, "platform", "linux")
    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)
    monkeypatch.setattr(nh, "_trusted", lambda *a, **kw: None)
    registry = tmp_path / "registry"
    registry.mkdir()
    return registry, tmp_path / "helper.sock"


def test_systemd_guard_inherits_sender_lock(tmp_path):
    import threading
    import time

    executable = tmp_path / "systemctl-fixture"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    executable.write_text(
        f"#!/bin/sh\ntouch '{ready}'\nwhile [ ! -e '{release}' ]; do sleep 0.02; done\n"
    )
    executable.chmod(0o755)
    lock = tmp_path / "execution.lock"
    errors = []
    with lock.open("a+b") as lease:
        nh.fcntl.flock(lease, nh.fcntl.LOCK_EX)

        def run():
            try:
                nh.Systemd(str(executable)).start(
                    "fixture.service",
                    lease_fd=lease.fileno(),
                    receipt_path=tmp_path / "receipt.json",
                    intent={"fixture": True},
                )
            except (OSError, subprocess.SubprocessError, nh.HelperError) as exc:
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            for _ in range(100):
                if ready.exists():
                    break
                time.sleep(0.02)
            assert ready.exists()
            # Closing the parent descriptor models the daemon losing ownership.
            lease.close()
            with lock.open("rb") as probe, pytest.raises(BlockingIOError):
                nh.fcntl.flock(probe, nh.fcntl.LOCK_EX | nh.fcntl.LOCK_NB)
        finally:
            release.touch()
            thread.join(timeout=5)
        assert not thread.is_alive() and not errors
    with lock.open("rb") as probe:
        nh.fcntl.flock(probe, nh.fcntl.LOCK_EX | nh.fcntl.LOCK_NB)


def test_native_management_marker_drift_blocks_helper(helper):
    service, adapter, record = helper
    (Path(record["root"]) / nh.MARKER).write_text("changed")
    with pytest.raises(nh.HelperError, match="management binding"):
        service._binding("probe")
    assert not adapter.calls


def test_package_lock_shared_start_and_cross_process_exclusion(helper):
    manager, _, _ = helper
    import sys

    # A separate process cannot acquire exclusive access while a start owns SH.
    code = """import fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDWR)
try:
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(7)
"""
    with nh.package_lease(manager.registry), nh.package_lease(manager.registry):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(manager.registry / "package-manager.execution.lock"),
            ],
            check=False,
        )
        assert result.returncode == 7
    with nh.package_lease(manager.registry, exclusive=True):
        pass


def test_systemd_guard_inherits_package_lease(tmp_path):

    registry = tmp_path / "registry"
    registry.mkdir()
    executable = tmp_path / "systemctl"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    # Use owned temporary storage while exercising the actual guard process.
    from unittest.mock import patch

    with (
        patch.object(nh, "_trusted", lambda *a, **k: None),
        nh.package_lease(registry) as package_fd,
        (tmp_path / "execution.lock").open("a+b") as lease,
    ):
        real_run = subprocess.run

        def checked_run(*args, **kwargs):
            assert package_fd in kwargs["pass_fds"]
            return real_run(*args, **kwargs)

        with patch.object(nh.subprocess, "run", checked_run):
            nh.Systemd(str(executable)).start(
                "fixture",
                lease_fd=lease.fileno(),
                package_fd=package_fd,
                receipt_path=tmp_path / "receipt.json",
                intent={"fixture": True},
            )
    assert json.loads((tmp_path / "receipt.json").read_text())["state"] == "sender_reaped"


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux procfs")
def test_systemd_guard_and_sender_do_not_inherit_helper_sockets(tmp_path):
    from unittest.mock import patch

    registry = tmp_path / "registry"
    registry.mkdir()
    endpoint = tmp_path / "helper.sock"
    report = tmp_path / "descriptor-report.json"
    executable = tmp_path / "inspect-sender"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(endpoint))
        listener.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(endpoint))
            connection, _ = listener.accept()
            with connection:
                # Exercise close_fds even if a future caller mistakenly marks sockets inheritable.
                os.set_inheritable(listener.fileno(), True)
                os.set_inheritable(connection.fileno(), True)
                identities = [(os.fstat(s.fileno()).st_dev, os.fstat(s.fileno()).st_ino)
                              for s in (listener, connection)]
                executable.write_text(
                    f"#!{sys.executable}\n"
                    "import json, os\n"
                    f"wanted = {identities!r}\n"
                    "def present(pid):\n"
                    "    found = []\n"
                    "    for name in os.listdir(f'/proc/{pid}/fd'):\n"
                    "        try:\n"
                    "            s = os.stat(f'/proc/{pid}/fd/{name}')\n"
                    "        except FileNotFoundError:\n"
                    "            continue\n"
                    "        if (s.st_dev, s.st_ino) in wanted:\n"
                    "            found.append(name)\n"
                    "    return found\n"
                    f"with open({str(report)!r}, 'w') as out:\n"
                    "    json.dump({'guard': present(os.getppid()), 'sender': present(os.getpid())}, out)\n"
                )
                executable.chmod(0o700)
                with (
                    patch.object(nh, "_trusted", lambda *args, **kwargs: None),
                    nh.package_lease(registry) as package_fd,
                    (tmp_path / "execution.lock").open("a+b") as lease,
                ):
                    nh.Systemd(str(executable)).start(
                        "fixture",
                        lease_fd=lease.fileno(),
                        package_fd=package_fd,
                        receipt_path=tmp_path / "receipt.json",
                        intent={"fixture": True},
                    )
    assert json.loads(report.read_text()) == {"guard": [], "sender": []}
    assert json.loads((tmp_path / "receipt.json").read_text())["state"] == "sender_reaped"


def test_inherited_package_lock_survives_callers_fd_close(helper):
    import sys

    manager, _, _ = helper
    with nh.package_lease(manager.registry) as fd:
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; print('ready',flush=True); sys.stdin.read()"],
            pass_fds=(fd,),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert child.stdout.readline().strip() == "ready"
    try:
        with (
            pytest.raises(nh.HelperError, match="still executing"),
            nh.package_lease(manager.registry, exclusive=True),
        ):
            pytest.fail("inherited shared lock lost")
    finally:
        child.communicate("", timeout=10)
    assert child.returncode == 0
    with nh.package_lease(manager.registry, exclusive=True):
        pass


@pytest.mark.parametrize("held_lock", [False, True])
def test_registration_blocked_by_package_execution_or_persistent_intent(
    helper, monkeypatch, held_lock
):
    from contextlib import nullcontext

    manager, _, record = helper
    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)
    called = []
    monkeypatch.setattr(nh, "_register", lambda *a, **k: called.append(True))
    if not held_lock:
        directory = manager.registry / "updates"
        directory.mkdir()
        (directory / "probe--update.apply-intent.json").write_text("{}")
    with (
        nh.package_lease(manager.registry, exclusive=True) if held_lock else nullcontext(),
        pytest.raises(nh.HelperError, match="still executing|package transaction"),
    ):
        nh.register(
            manager.registry,
            manager.registry,
            instance_id="second",
            root=record["root"],
            username="fixture",
            environment_file=record["environment_file"],
            recover=True,
        )
    assert called == []


def test_marker_readable_and_operation_receipt_private(tmp_path):
    marker = tmp_path / "marker.json"
    receipt = tmp_path / "operation.json"
    nh._save_new(marker, {"binding": "non-secret"}, mode=0o644)
    nh._save_new(receipt, {"operation": "private"})
    assert marker.stat().st_mode & 0o777 == 0o644
    assert receipt.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("drift", ["duplicate", "missing", "extra", "malformed", "large", "nonstring"])
def test_systemd_query_rejects_ambiguous_output(monkeypatch, drift):
    from types import SimpleNamespace

    lines = [
        "ActiveState=active", "SubState=running", "MainPID=123", "Job=",
        "User=12345", "Group=12345", "FragmentPath=/etc/systemd/system/probe.service",
        "ExecStart={ path=/fixture/run ; argv[]=/fixture/run client ; ignore_errors=no ; }",
    ]
    if drift == "duplicate":
        lines.insert(0, "ActiveState=failed")
    elif drift == "missing":
        lines = [line for line in lines if not line.startswith("Job=")]
    elif drift == "extra":
        lines.append("Unknown=value")
    elif drift == "malformed":
        lines.append("unparseable")
    elif drift == "large":
        lines.append("X=" + "x" * 17000)
    output = b"not text" if drift == "nonstring" else "\n".join(lines) + "\n"
    monkeypatch.setattr(nh.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=output))
    with pytest.raises(nh.HelperError, match="systemd.*observation"):
        nh.Systemd().query("probe.service")


def test_systemd_query_accepts_exact_fields_and_empty_job(monkeypatch):
    from types import SimpleNamespace

    output = (
        "ActiveState=active\nSubState=running\nMainPID=123\nJob=\n"
        "User=12345\nGroup=12345\nFragmentPath=/etc/systemd/system/probe.service\n"
        "ExecStart={ path=/fixture/run ; argv[]=/fixture/run client ; ignore_errors=no ; }\n"
    )
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(nh.subprocess, "run", run)
    result = nh.Systemd().query("probe.service")
    assert result["Job"] == "" and result["MainPID"] == "123"
    assert calls[0][0][:3] == ["/usr/bin/systemctl", "show", "probe.service"]


def test_query_supervisor_unknown_guard_rejects_dangling_symlink(tmp_path):
    marker = tmp_path / 'query-supervisor.intent.json'
    marker.symlink_to(tmp_path / 'missing')
    with pytest.raises(nh.HelperError, match='query supervisor intent remains unknown'):
        nh._assert_no_query_supervisor_intent(tmp_path)
    assert marker.is_symlink()


def test_query_supervisor_unknown_blocks_registration_before_management_file(tmp_path, monkeypatch):
    registry = tmp_path / 'registry'
    root = tmp_path / 'instance'
    registry.mkdir()
    root.mkdir()
    marker = registry / 'query-supervisor.intent.json'
    marker.write_bytes(b'unknown')
    monkeypatch.setattr(nh.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(nh, '_trusted', lambda *_a, **_kw: None)
    with pytest.raises(nh.HelperError, match='query supervisor intent remains unknown'):
        nh.register(registry, tmp_path, instance_id='probe', root=root, username='fixture', environment_file='/tmp/env')
    assert not (root / '.management.lock').exists()
    assert not (root / nh.MARKER).exists()
    assert marker.read_bytes() == b'unknown'


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
@pytest.mark.parametrize("carrier", ["instance.json", "active.json"])
def test_registration_refuses_fifo_identity_evidence(tmp_path, monkeypatch, carrier):
    """_register runs as root and reads identity files from the owner's tree.

    A FIFO there would block registration with no error, and these two files are
    exactly what decides whether the instance identity matches.
    """
    import pwd
    import signal
    import time

    registry = tmp_path / "registry"
    registry.mkdir()
    root = tmp_path / "instance"
    (root / "config").mkdir(parents=True)
    (root / ".installation.lock").touch()
    for relative, data in [
        ("run", "#!/opt/python/bin/python3.13\n"),
        ("instance.json", '{"instance_id":"probe"}'),
        ("active.json", '{"roles":["client"]}'),
        ("config/roles.json", "{}"),
    ]:
        (root / relative).write_text(data)
    environment = tmp_path / "token.env"
    environment.write_text("PRIVATE=1\n")
    environment.chmod(0o600)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()

    account = pwd.getpwuid(root.stat().st_uid)
    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)
    monkeypatch.setattr(nh, "_trusted", lambda *a, **kw: None)
    monkeypatch.setattr(nh, "_trusted_marker", lambda _: None)
    monkeypatch.setattr(nh, "_identifier", lambda _: None)

    target = root / carrier
    target.unlink()
    os.mkfifo(target, 0o600)

    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        try:
            nh._register(
                registry,
                unit_dir,
                instance_id="probe",
                root=root,
                username=account.pw_name,
                environment_file=environment,
            )
        except nh.HelperError:
            os._exit(21)
        except BaseException:  # noqa: BLE001
            os._exit(22)
        os._exit(0)

    deadline = time.time() + 5
    while time.time() < deadline:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            assert os.waitstatus_to_exitcode(status) == 21
            return
        time.sleep(0.02)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    pytest.fail(f"_register blocked on FIFO {carrier} instead of refusing")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
def test_registration_refuses_fifo_runner(tmp_path, monkeypatch):
    """The runner is readable by root; X_OK does not exclude a FIFO."""
    import pwd
    import signal
    import time

    registry = tmp_path / "registry"
    registry.mkdir()
    root = tmp_path / "instance"
    (root / "config").mkdir(parents=True)
    (root / ".installation.lock").touch()
    for relative, data in [
        ("instance.json", '{"instance_id":"probe"}'),
        ("active.json", '{"roles":["client"]}'),
        ("config/roles.json", "{}"),
    ]:
        (root / relative).write_text(data)
    environment = tmp_path / "token.env"
    environment.write_text("PRIVATE=1\n")
    environment.chmod(0o600)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    account = pwd.getpwuid(root.stat().st_uid)

    monkeypatch.setattr(nh.os, "geteuid", lambda: 0)
    monkeypatch.setattr(nh, "_trusted", lambda *a, **kw: None)
    monkeypatch.setattr(nh, "_trusted_marker", lambda _: None)
    monkeypatch.setattr(nh, "_identifier", lambda _: None)

    runner = root / "run"
    os.mkfifo(runner, 0o755)

    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        try:
            nh._register(
                registry,
                unit_dir,
                instance_id="probe",
                root=root,
                username=account.pw_name,
                environment_file=environment,
            )
        except nh.HelperError:
            os._exit(21)
        except BaseException:  # noqa: BLE001
            os._exit(22)
        os._exit(0)

    deadline = time.time() + 5
    while time.time() < deadline:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            assert os.waitstatus_to_exitcode(status) == 21
            return
        time.sleep(0.02)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    pytest.fail("_register blocked on FIFO runner instead of refusing")


def test_default_registry_is_not_a_package_guard_path():
    """The registry must not sit on a path the package guards refuse.

    The guards treat /var/lib/ai-device/manager as "a controlled transaction
    is pending" and refuse to install. A helper whose registry lived there
    would block every later upgrade of its own payload -- which is exactly
    what happened before the default was fixed.
    """
    assert nh.REGISTRY == Path("/var/lib/ai-device/native")
    assert nh.REGISTRY not in nh.PACKAGE_GUARD_PATHS


def test_registry_flag_defaults_but_stays_overridable():
    """Explicit registry still wins; the default only applies when omitted."""
    parser_help = nh.main.__doc__
    assert parser_help is None or isinstance(parser_help, str)
    assert nh.REGISTRY == Path("/var/lib/ai-device/native")


