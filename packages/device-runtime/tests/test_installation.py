import fcntl
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from test_bundle import make_wheel

from device_runtime import installation as module
from device_runtime.bundle import COMPONENTS, BundleError, build_bundle
from device_runtime.installation import InstallationError, install_instance, rebuild_instance

REAL_INVOKE = module._invoke
REAL_UV = shutil.which("uv")


@pytest.fixture
def delivery(tmp_path):
    wheels = {c: make_wheel(tmp_path, c) for c in COMPONENTS}
    wheel = wheels["device"]
    with zipfile.ZipFile(wheel) as archive:
        entries = {n: archive.read(n) for n in archive.namelist()}
    metadata = next(n for n in entries if n.endswith("/METADATA"))
    entries[metadata] += (
        b"Provides-Extra: client\n"
        b'Requires-Dist: ai-device-bash==0.2.0; extra == "client"\n'
        b'Requires-Dist: ai-device-fs==0.2.0; extra == "client"\n'
    )
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    constraints = tmp_path / "pins.txt"
    constraints.write_text("pydantic==2.13.5\n")
    bundle = tmp_path / "bundle"
    build_bundle(
        bundle,
        release_version="0.2.0",
        wheels=wheels,
        constraints=constraints,
        sources={c: "a" * 40 for c in COMPONENTS},
    )
    return bundle


@pytest.fixture
def fake_uv(monkeypatch):
    commands = []
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.shutil, "which", lambda _: "/fake/uv")
    monkeypatch.setattr(module, "_invoke", lambda args, **kwargs: commands.append((args, kwargs)))
    return commands


def make_runtime(root, python=None):
    """Build a minimal runtime tree to hand to install_instance.

    Native installs always pass a runtime payload; supplying one here makes the
    fixture exercise the launcher layout instead of the legacy single file.
    """
    if python is None:
        python = sys.executable
    source = Path(root).parent / (Path(root).name + "-runtime-src")
    target = source / "bin" / "python3.13"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"#!/bin/sh\nexec {python} \"$@\"\n")
    target.chmod(0o755)
    return source


def create(root, delivery, roles=("client",), runtime=True):
    return install_instance(
        delivery,
        root=root,
        instance_id="isolated-mac",
        roles=roles,
        server_url="http://127.0.0.1:28765",
        port=28765,
        runtime_source=make_runtime(root) if runtime else None,
    )


def add_runtime(root, release=None, python=None):
    """Give a release the interpreter the launcher resolves from the pointer.

    The launcher execs releases/<release>/runtime/bin/python3.13, so tests that
    start ``run`` need that path to exist; without it the entry fails closed.
    """
    if release is None:
        release = (root / "current").read_text().strip()
    target = root / "releases" / release / "runtime" / "bin" / "python3.13"
    target.parent.mkdir(parents=True, exist_ok=True)
    if python is None:
        python = sys.executable
    if not target.exists():
        target.write_text(f"#!/bin/sh\nexec {python} \"$@\"\n")
        target.chmod(0o755)
    return target


def persistent(root):
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
        and (p.parts[len(root.parts)] in ("config", "state") or p.name in ("instance.json", "run"))
    }


@pytest.mark.parametrize("operation", ["install", "rebuild", "prepare"])
def test_environment_uses_bootstrap_python_not_uv_discovery(
    tmp_path, delivery, fake_uv, monkeypatch, operation
):
    from pathlib import Path

    from device_runtime import upgrader

    root = tmp_path / "instance"
    if operation != "install":
        create(root, delivery, runtime=False)
        fake_uv.clear()
    # Simulate a transient bootstrap venv backed by a durable interpreter.
    interpreter = tmp_path / "shared python" / "python3.13"
    interpreter.parent.mkdir()
    interpreter.symlink_to(Path(sys.executable).resolve())
    bootstrap = tmp_path / "bootstrap-python"
    bootstrap.symlink_to(interpreter)
    monkeypatch.setattr(module.sys, "executable", str(bootstrap))
    monkeypatch.setenv("UV_PYTHON", "/root/private/python")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "/root/private/managed")
    if operation == "install":
        create(root, delivery, ("center", "client", "mcp"), runtime=False)
    elif operation == "rebuild":
        rebuild_instance(root)
    else:
        upgrader.prepare(root, delivery, "python-selection")
    commands = [args for args, _ in fake_uv if args[2] == "venv"]
    assert len(commands) == (3 if operation == "install" else 1)
    for args in commands:
        assert args[args.index("--python") + 1] == str(interpreter.resolve())
        assert "--no-python-downloads" in args


@pytest.mark.parametrize("version", [(3, 12), (3, 14)])
def test_wrong_bootstrap_python_rejected_before_release_creation(
    tmp_path, delivery, fake_uv, monkeypatch, version
):
    monkeypatch.setattr(module.sys, "version_info", version)
    root = tmp_path / "instance"
    with pytest.raises(InstallationError, match="CPython 3.13"):
        create(root, delivery)
    assert not list((root / "releases").glob("*"))
    assert not fake_uv


@pytest.mark.skipif(not REAL_UV, reason="uv is required for real interpreter selection")
def test_real_uv_venv_uses_bootstrap_base_with_hostile_uv_environment(
    tmp_path, delivery, fake_uv, monkeypatch
):
    from pathlib import Path

    monkeypatch.setenv("UV_PYTHON", "/root/private/nonexistent-python")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "/root/private/nonexistent-managed")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    commands = []

    def invoke(args, **kwargs):
        commands.append(args)
        if args[2] == "venv":
            REAL_INVOKE([REAL_UV, *args[1:]], **kwargs)

    monkeypatch.setattr(module, "_invoke", invoke)
    root = tmp_path / "instance"
    # This checks the venv base against the bootstrap interpreter, so it must
    # not route the environment through a copied private runtime.
    active = create(root, delivery, runtime=False)
    python = root / "releases" / active["release"] / "client/venv/bin/python"
    actual = json.loads(
        subprocess.check_output(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                "import sys,json;print(json.dumps([sys.version_info[:2],sys.base_prefix]))",
            ],
            text=True,
        )
    )
    assert actual == [[3, 13], sys.base_prefix]
    assert python.resolve() == Path(sys.executable).resolve()
    assert commands[0][commands[0].index("--python") + 1] == str(python.resolve())


def test_missing_bootstrap_python_rejected_before_release_creation(
    tmp_path, delivery, fake_uv, monkeypatch
):
    monkeypatch.setattr(module.sys, "executable", str(tmp_path / "missing-python"))
    root = tmp_path / "instance"
    with pytest.raises(InstallationError, match="executable at a durable shared path"):
        create(root, delivery)
    assert not list((root / "releases").glob("*"))
    assert not fake_uv


def test_three_roles_and_stable_entry(tmp_path, delivery, fake_uv):
    root = tmp_path / "instance with spaces"
    result = create(root, delivery, ("center", "mcp", "client"))
    assert result["roles"] == ["center", "client", "mcp"]
    assert os.access(root / "run", os.X_OK)
    config = json.loads((root / "config/roles.json").read_text())
    assert config["client"] == {"server_url": "ws://127.0.0.1:28765", "device_id": "isolated-mac"}
    assert config["mcp"]["server_url"] == "http://127.0.0.1:28765"
    assert config["center"] == {"port": 28765}
    installs = [args for args, _ in fake_uv if "install" in args]
    assert len(installs) == 3
    for args in installs:
        assert "--constraints" in args and "--only-binary" in args
        assert args[args.index("--constraints") + 1] == "../bundle/constraints.txt"
        assert all(".whl" not in arg or root.as_uri() in arg for arg in args)
        device = next(arg for arg in args if "ai-device[" in arg)
        if "[client]" in device:
            assert sum(".whl" in arg for arg in args) == 3
        else:
            assert sum(".whl" in arg for arg in args) == 1
    assert all(kwargs["cwd"].is_relative_to(root) for _, kwargs in fake_uv)


def test_install_existing_instance_remains_untouched(tmp_path, delivery, fake_uv):
    root = tmp_path / "existing"
    root.mkdir()
    (root / "keep").write_bytes(b"preserve")
    with pytest.raises(InstallationError, match="destination already exists"):
        install_instance(delivery, instance_id="fixture", roles=["client"], root=root,
                         server_url="http://127.0.0.1:9")
    assert sorted(p.name for p in root.iterdir()) == ["keep"]
    assert (root / "keep").read_bytes() == b"preserve"


def test_install_directory_creation_permission_error_is_not_existing(tmp_path, delivery, fake_uv, monkeypatch):
    root = tmp_path / "denied"
    original = module.Path.mkdir

    def denied(path, *args, **kwargs):
        if path == root:
            raise PermissionError(13, "fixture failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(module.Path, "mkdir", denied)
    with pytest.raises(InstallationError, match=r"cannot create instance directory \(errno 13\)") as captured:
        install_instance(delivery, instance_id="fixture", roles=["client"], root=root,
                         server_url="http://127.0.0.1:9")
    assert isinstance(captured.value.__cause__, PermissionError)
    assert not root.exists()


@pytest.mark.parametrize("error_number", [13, 28])
def test_install_io_failure_keeps_partial_state_and_reports_cause(tmp_path, delivery, fake_uv, monkeypatch, error_number):
    root = tmp_path / "partial-instance"

    def fail_prepare(root, *args, **kwargs):
        (root / "partial-evidence").write_bytes(b"preserve")
        raise OSError(error_number, "fixture failure")

    monkeypatch.setattr(module, "_prepare", fail_prepare)
    with pytest.raises(InstallationError, match="partial.*retained") as captured:
        install_instance(delivery, instance_id="fixture", roles=["client"], root=root,
                         server_url="http://127.0.0.1:9")
    assert isinstance(captured.value.__cause__, OSError)
    assert captured.value.__cause__.errno == error_number
    assert str(error_number) in str(captured.value)
    assert (root / "partial-evidence").read_bytes() == b"preserve"
    assert not (root / "active.json").exists()


def test_rebuild_preserves_identity_configuration_journal_output(tmp_path, delivery, fake_uv):
    root = tmp_path / "instance"
    old = create(root, delivery)
    (root / "state/client/operations.sqlite3").write_bytes(b"durable unknown operation")
    output = root / "state/client/output/task-001"
    output.parent.mkdir()
    output.write_bytes(b"original output\x00\xff")
    (root / "config/private.env").write_text("not-a-real-secret-fixture")
    before = persistent(root)
    rebuilt = rebuild_instance(root)
    assert rebuilt["release"] != old["release"]
    assert rebuilt["bundle_sha256"] == old["bundle_sha256"]
    assert persistent(root) == before
    assert (root / "releases" / old["release"]).is_dir()
    assert json.loads((root / "active.json").read_text())["release"] == rebuilt["release"]


@pytest.mark.parametrize("role_index", [0, 1, 2])
def test_preparation_failure_never_publishes_or_overwrites_data(
    tmp_path, delivery, fake_uv, monkeypatch, role_index
):
    root = tmp_path / "instance"
    create(root, delivery, ("center", "mcp", "client"))
    before = persistent(root)
    active = (root / "active.json").read_bytes()
    calls = 0

    def fail(args, **kwargs):
        nonlocal calls
        if "install" in args:
            if calls == role_index:
                raise InstallationError("injected failure")
            calls += 1

    monkeypatch.setattr(module, "_invoke", fail)
    with pytest.raises(InstallationError, match="injected"):
        rebuild_instance(root)
    assert (root / "active.json").read_bytes() == active
    assert persistent(root) == before


def test_existing_empty_or_populated_directory_is_never_used(tmp_path, delivery, fake_uv):
    root = tmp_path / "existing"
    root.mkdir()
    for populated in (False, True):
        if populated:
            (root / "important").write_text("keep")
        before = list(root.iterdir())
        with pytest.raises(InstallationError, match="already exist"):
            create(root, delivery)
        assert list(root.iterdir()) == before
    assert not fake_uv


def test_locked_instance_rejects_rebuild(tmp_path, delivery, fake_uv):
    root = tmp_path / "instance"
    create(root, delivery)
    before = (root / "active.json").read_bytes()
    with (root / ".installation.lock").open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        with pytest.raises(InstallationError, match="running"):
            rebuild_instance(root)
    assert (root / "active.json").read_bytes() == before


def test_changed_or_upgrade_bundle_is_rejected(tmp_path, delivery, fake_uv):
    root = tmp_path / "instance"
    original = create(root, delivery)
    manifest = delivery / "manifest.json"
    value = json.loads(manifest.read_text())
    value["release_version"] = "0.3.0"
    manifest.write_text(json.dumps(value))
    with pytest.raises(InstallationError, match="identical"):
        rebuild_instance(root, bundle=delivery)
    assert json.loads((root / "active.json").read_text()) == {
        k: v for k, v in original.items() if k != "instance_root"
    }


@pytest.mark.parametrize("path", ["config/roles.json", "state/client"])
def test_missing_persistent_data_is_not_recreated(tmp_path, delivery, fake_uv, path):
    root = tmp_path / "instance"
    create(root, delivery)
    target = root / path
    if target.is_dir():
        target.rmdir()
    else:
        target.unlink()
    before = len(fake_uv)
    with pytest.raises(InstallationError, match="missing"):
        rebuild_instance(root)
    assert len(fake_uv) == before and not target.exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://user:password@localhost",
        "ws://localhost",
        "http://localhost?token=x",
        "",
    ],
)
def test_invalid_center_url_rejected_before_creating_root(tmp_path, delivery, fake_uv, url):
    root = tmp_path / "instance"
    with pytest.raises(ValueError):
        install_instance(delivery, root=root, roles=["client"], instance_id="mac", server_url=url)
    assert not root.exists()


@pytest.mark.parametrize("identity", ["../pilot", "", "..", "x/y", "x" * 65])
def test_invalid_identity_rejected(tmp_path, delivery, fake_uv, identity):
    root = tmp_path / "instance"
    with pytest.raises(InstallationError, match="ID"):
        install_instance(delivery, root=root, roles=["center"], instance_id=identity)
    assert not root.exists()


def test_platform_and_uv_fail_closed(tmp_path, delivery, fake_uv, monkeypatch):
    root = tmp_path / "instance"
    monkeypatch.setattr(module.sys, "platform", "win32")
    with pytest.raises(InstallationError, match="macOS"):
        create(root, delivery)
    assert not root.exists()
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    with pytest.raises(InstallationError, match="uv"):
        create(root, delivery)
    assert not (root / "active.json").exists()


@pytest.mark.parametrize(
    "requirement",
    [
        'ai-device-bash==0.1.0; extra == "client"',
        'ai-device-bash>=0.2.0; extra == "client"',
        "ai-device-bash==0.2.0",
        'ai-device-bash==0.2.0; extra != "mcp"',
    ],
)
def test_dependency_contract_rejects_wrong_version_or_role(delivery, requirement):
    manifest = json.loads((delivery / "manifest.json").read_text())
    wheel = delivery / manifest["components"]["device"]["path"]
    with zipfile.ZipFile(wheel) as archive:
        entries = {n: archive.read(n) for n in archive.namelist()}
    metadata = next(n for n in entries if n.endswith("/METADATA"))
    entries[metadata] = entries[metadata].replace(
        b'ai-device-bash==0.2.0; extra == "client"', requirement.encode()
    )
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    with pytest.raises(InstallationError, match="client-only"):
        module._client_contract(delivery, manifest)


def test_corrupt_bundle_rejected_before_writes(tmp_path, delivery, fake_uv):
    root = tmp_path / "instance"
    (delivery / "constraints.txt").write_text("changed")
    with pytest.raises(BundleError):
        create(root, delivery)
    assert not root.exists()


def test_runner_error_is_controlled_and_keeps_config_private(tmp_path, delivery, fake_uv):
    root = tmp_path / "instance"
    create(root, delivery)
    add_runtime(root)
    result = subprocess.run(
        [str(root / "run"), "mcp"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2 and "Traceback" not in result.stderr


def test_cli_existing_destination_returns_two(tmp_path, delivery, fake_uv):
    root = tmp_path / "exists"
    root.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "device_runtime.cli",
            "instance",
            "install",
            "--root",
            str(root),
            "--instance-id",
            "mac",
            "--role",
            "center",
            "--bundle",
            str(delivery),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and "Traceback" not in result.stderr
    assert not list(root.iterdir())


def test_runner_holds_lease_and_forwards_termination(tmp_path, delivery, fake_uv):
    import time

    root = tmp_path / "instance with spaces"
    installed = create(root, delivery, ("center",))
    executable = root / "releases" / installed["release"] / "center/venv/bin/python"
    executable.parent.mkdir(parents=True)
    ready = tmp_path / "ready"
    done = tmp_path / "done"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os,signal,time\nfrom pathlib import Path\n"
        "def stop(*args):\n"
        "    Path(os.environ['TEST_DONE']).write_text('stopped')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        "Path(os.environ['TEST_READY']).write_text('ready')\n"
        "while True: time.sleep(.02)\n"
    )
    executable.chmod(0o755)
    add_runtime(root)
    process = subprocess.Popen(
        [str(root / "run"), "center"],
        env={**os.environ, "TEST_READY": str(ready), "TEST_DONE": str(done)},
    )
    try:
        for _ in range(100):
            assert process.poll() is None
            if ready.exists():
                break
            time.sleep(0.02)
        else:
            pytest.fail("isolated child not ready")
        with pytest.raises(InstallationError, match="running"):
            rebuild_instance(root)
        process.terminate()
        assert process.wait(timeout=5) == 0
        assert done.read_text() == "stopped"
        with module._lock(root):
            pass
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_invalid_release_pointer_cannot_escape_root(tmp_path, delivery, fake_uv):
    root = tmp_path / "instance"
    create(root, delivery)
    active = json.loads((root / "active.json").read_text())
    active["release"] = ".."
    (root / "active.json").write_text(json.dumps(active))
    count = len(fake_uv)
    with pytest.raises(InstallationError, match="pointer"):
        rebuild_instance(root)
    assert len(fake_uv) == count


@pytest.mark.parametrize("signum", [15, 2], ids=["SIGTERM", "SIGINT"])
def test_termination_while_popen_has_child_but_has_not_returned(
    tmp_path, delivery, fake_uv, signum
):
    import signal

    root = tmp_path / "early instance with spaces"
    installed = create(root, delivery, ("center",))
    executable = root / "releases" / installed["release"] / "center/venv/bin/python"
    executable.parent.mkdir(parents=True)
    ready = tmp_path / "early-ready"
    done = tmp_path / "early-done"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os,signal,time\nfrom pathlib import Path\n"
        "def stop(*args):\n"
        "    Path(os.environ['TEST_DONE']).write_text('stopped')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        "signal.signal(signal.SIGINT,stop)\n"
        "Path(os.environ['TEST_READY']).write_text(str(os.getpid()))\n"
        "while True: time.sleep(.02)\n"
    )
    executable.chmod(0o755)
    # Pause Popen after the REAL child exists, but before the runner can assign it.
    # This deterministically exercises the original handler-registration window.
    injection = (
        "import os,signal,subprocess,time\nfrom pathlib import Path\n"
        "original = subprocess.Popen\n"
        "def delayed(*args,**kwargs):\n"
        "    child = original(*args,**kwargs)\n"
        "    for _ in range(200):\n"
        "        if Path(os.environ['TEST_READY']).exists(): break\n"
        "        time.sleep(.01)\n"
        "    else: raise RuntimeError('owned child was not ready')\n"
        f"    os.kill(os.getpid(), {signum})\n"
        "    return child\n"
        "subprocess.Popen = delayed\n"
        f"entry = {str(root / 'run-runner')!r}\n"
        f"release = {installed['release']!r}\n"
        "exec(compile(Path(entry).read_text(),entry,'exec'),"
        "{'__file__':entry,'__name__':'__main__'})\n"
    )
    add_runtime(root)
    process = subprocess.Popen(
        [sys.executable, "-c", injection, installed["release"], "center"],
        env={**os.environ, "TEST_READY": str(ready), "TEST_DONE": str(done)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(timeout=6)
        assert process.returncode == 0, (process.returncode, stdout, stderr)
        assert done.read_text() == "stopped"
        # Child AND launcher have exited, releasing all inherited shared leases.
        with module._lock(root):
            pass
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if ready.exists() and not done.exists():
            # Only the child created by this exact fixture, never a real instance.
            os.kill(int(ready.read_text()), signal.SIGTERM)


@pytest.fixture
def metadata_environment(tmp_path):
    """Independent venv + metadata; executes the REAL isolated verifier, not _invoke mocks."""
    import importlib.metadata
    import shutil
    from pathlib import Path

    import packaging

    uv = REAL_UV
    packaging_version = importlib.metadata.version("packaging")

    def make(home, role="center", missing="uvicorn", output_map_support="undeclared"):
        venv = home / "venv"
        if venv.exists():
            assert venv.is_relative_to(tmp_path)  # Only this test's placeholder environment.
            shutil.rmtree(venv)
        subprocess.run(
            [uv, "--no-config", "venv", "--python", sys.executable, str(venv)],
            check=True,
            capture_output=True,
        )
        python = venv / "bin" / "python"
        site = Path(
            subprocess.check_output(
                [
                    str(python),
                    "-I",
                    "-B",
                    "-c",
                    "import sysconfig; print(sysconfig.get_path('purelib'))",
                ],
                text=True,
            ).strip()
        )
        shutil.copytree(Path(packaging.__file__).parent, site / "packaging")
        components = {c: {"distribution": COMPONENTS[c][0], "version": "0.2.0"} for c in COMPONENTS}
        pins = {
            "packaging": packaging_version,
            "uvicorn": "0.35.0",
            "transport-lib": "1.0.0",
            "leaf-lib": "1.0.0",
            "pillow": "1.0.0",
        }

        def dist(name, version, requirements=()):
            directory = site / (name.replace("-", "_") + "-" + version + ".dist-info")
            directory.mkdir()
            (directory / "METADATA").write_text(
                "Metadata-Version: 2.3\nName: "
                + name
                + "\nVersion: "
                + version
                + "\n"
                + "".join("Requires-Dist: " + r + "\n" for r in requirements)
            )

        dist("packaging", packaging_version)
        dist(
            "ai-device",
            "0.2.0",
            [
                "packaging==" + packaging_version,
                'uvicorn>=0.35.0; extra == "server"',
                'transport-lib[io]>=1; extra == "mcp"',
                'ai-device-bash==0.2.0; extra == "client"',
                'ai-device-fs==0.2.0; extra == "client"',
                'missing-unused>=1; extra == "unused"',
                'missing-platform>=1; python_version < "1"',
            ],
        )
        if missing != "uvicorn" and role == "center":
            dist("uvicorn", "0.35.0")
            (site / "uvicorn.py").write_text("def run(*args, **kwargs): pass\n")
        if role in ("mcp", "client"):
            dist(
                "transport-lib",
                "1.0.0",
                ['leaf-lib>=1; extra == "io"', 'missing-unused>=1; extra == "unused"'],
            )
            if missing != "leaf-lib":
                dist("leaf-lib", "1.0.0", ["transport-lib[io]>=1"])  # Finite extra cycle.
        if role == "client":
            dist("ai-device-bash", "0.2.0", ["transport-lib[io]>=1"])
            dist("ai-device-fs", "0.2.0")
            dist("pillow", "1.0.0")
        modules = {
            "device_runtime/__init__.py": "",
            "device_runtime/server.py": "def create_app(): pass\n",
            "device_runtime/mcp_server.py": "def create_mcp(): pass\n",
            "device_runtime/executor.py": "class LocalExecutor: pass\n",
        }
        if role == "client":
            if output_map_support == "current":
                from device_runtime import limits, tasks

                modules["device_runtime/limits.py"] = Path(limits.__file__).read_text()
                # Real current reader, with no Journal instantiation in the output probe.
                modules["device_runtime/tasks.py"] = Path(tasks.__file__).read_text()
                modules["device_runtime/journal.py"] = "class Journal: pass\n"
            elif output_map_support != "missing_module":
                modules["device_runtime/tasks.py"] = (
                    "SUPPORTED_OUTPUT_MAP_FORMATS = (2,)\n"
                    if output_map_support == "unsupported"
                    else "# Legacy tasks module: no output-map support declaration.\n"
                )
            modules.update(
                {
                    "mcp_server_bash/__init__.py": "",
                    "mcp_server_bash/core.py": "class ExecutionRuntime: pass\n",
                    "mcp_server_fs/__init__.py": "",
                    "mcp_server_fs/core.py": "def read_image(): pass\n",
                    "PIL/__init__.py": "",
                }
            )
        for name, content in modules.items():
            path = site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return python, {"components": components, "constraints": {"pins": pins}}

    return make


@pytest.mark.parametrize(
    "role,missing", [("center", "uvicorn"), ("mcp", "leaf-lib"), ("client", "leaf-lib")]
)
def test_role_extra_missing_dependency_rejected_by_real_verifier(
    tmp_path, metadata_environment, role, missing
):
    python, manifest = metadata_environment(tmp_path / "independent", role, missing)
    with pytest.raises(InstallationError):
        module._verify_environment(python, role, manifest, cwd=tmp_path, log=None)


@pytest.mark.parametrize("role", ["center", "mcp", "client"])
def test_role_extra_closed_graph_does_not_enable_unrequested_extras(
    tmp_path, metadata_environment, role
):
    python, manifest = metadata_environment(tmp_path / "independent", role, missing=None)
    module._verify_environment(python, role, manifest, cwd=tmp_path, log=None)


@pytest.fixture
def extra_bundle(tmp_path, delivery, metadata_environment):
    _, independent = metadata_environment(tmp_path / "metadata-seed", missing=None)
    pins = tmp_path / "independent-pins.txt"
    pins.write_text(
        "".join(
            f"{name}=={version}\n" for name, version in independent["constraints"]["pins"].items()
        )
    )
    manifest = module.inspect_bundle(delivery)
    target = tmp_path / "extra-bundle"
    build_bundle(
        target,
        release_version="0.2.0",
        wheels={c: delivery / m["path"] for c, m in manifest["components"].items()},
        constraints=pins,
        sources={c: m["source_commit"] for c, m in manifest["components"].items()},
    )
    return target


@pytest.mark.parametrize("action", ["install", "rebuild"])
def test_install_rebuild_role_extra_failure_keeps_active(
    tmp_path, extra_bundle, metadata_environment, fake_uv, monkeypatch, action
):
    root = tmp_path / "instance"
    if action == "rebuild":
        create(root, extra_bundle, roles=("center",))
        before = (root / "active.json").read_bytes()
        data = persistent(root)

    def preparation(args, *, cwd, log):
        if args[1:3] == ["--no-config", "venv"]:
            metadata_environment(cwd, missing="uvicorn")
        elif len(args) > 1 and args[1] == "-I":
            REAL_INVOKE(args, cwd=cwd, log=log)  # Actual verifier subprocess.
        # Wheel install/pip check is simulated; the metadata verifier is not.

    monkeypatch.setattr(module, "_invoke", preparation)
    with pytest.raises(InstallationError):
        if action == "install":
            create(root, extra_bundle, roles=("center",))
        else:
            rebuild_instance(root)
    if action == "install":
        assert not (root / "active.json").exists()
    else:
        assert (root / "active.json").read_bytes() == before
        assert persistent(root) == data


@pytest.mark.parametrize("support", ["missing_module", "undeclared", "unsupported"])
def test_real_client_verifier_requires_declared_output_map_format(
    tmp_path, metadata_environment, support
):
    python, manifest = metadata_environment(
        tmp_path / "required-map-probe", role="client", missing=None, output_map_support=support
    )
    # Versions, imports and dependencies are all valid; only the reader contract is missing.
    module._verify_environment(python, "client", manifest, cwd=tmp_path, log=None)
    with pytest.raises(InstallationError):
        module._verify_environment(
            python, "client", manifest, cwd=tmp_path, log=None, required_output_map_format=1
        )


def test_real_client_verifier_accepts_current_output_map_format(tmp_path, metadata_environment):
    python, manifest = metadata_environment(
        tmp_path / "supported-map-probe", role="client", missing=None, output_map_support="current"
    )
    module._verify_environment(
        python, "client", manifest, cwd=tmp_path, log=None, required_output_map_format=1
    )


@pytest.mark.parametrize("role", ["center", "mcp"])
def test_required_output_map_probe_is_client_only(tmp_path, metadata_environment, role):
    python, manifest = metadata_environment(tmp_path / role, role=role, missing=None)
    module._verify_environment(
        python, role, manifest, cwd=tmp_path, log=None, required_output_map_format=1
    )


@pytest.mark.parametrize("invalid", [None, 0, -1, True, 1.5, "8"])
def test_runner_client_concurrency_config(tmp_path, delivery, fake_uv, invalid):
    root = tmp_path / "instance"
    installed = create(root, delivery)
    config_path = root / "config/roles.json"
    config = json.loads(config_path.read_text())
    config["client"].update(
        max_concurrent_requests=12, max_concurrent_queries=3, max_active_tasks=16
    )
    if invalid is not None:
        config["client"]["max_active_tasks"] = invalid
    config_path.write_text(json.dumps(config))
    executable = root / "releases" / installed["release"] / "client/venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        "#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n"
    )
    executable.chmod(0o755)
    # The launcher resolves its interpreter from the pointer, so the release
    # needs that path even though these cases only inspect the venv argv.
    add_runtime(root, release=installed["release"])
    result = subprocess.run(
        [str(root / "run"), "client"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if invalid is not None:
        assert result.returncode == 2
        assert not result.stdout
    else:
        assert result.returncode == 0, result.stderr
        argv = json.loads(result.stdout)
        for flag, value in [
            ("--max-concurrent-requests", "12"),
            ("--max-concurrent-queries", "3"),
            ("--max-active-tasks", "16"),
        ]:
            assert argv[argv.index(flag) + 1] == value


def test_native_runner_is_written_before_active(tmp_path, delivery, fake_uv, monkeypatch):
    root = tmp_path / "native-instance"
    python = module.Path(sys.executable).resolve()
    original = module._json

    def save(path, value):
        if path == root / "active.json":
            # The stable entry is the launcher, and its interpreter choice comes
            # from the pointer, so the shebang is no longer the call-site python.
            assert (root / "run").read_text() == module.LAUNCHER
            assert (root / "run-runner").read_text() == module.RUNNER_BODY
            assert (root / "current").read_text().strip() == value["release"]
        return original(path, value)

    monkeypatch.setattr(module, "_json", save)
    install_instance(
        delivery,
        root=root,
        instance_id="native",
        roles=["client"],
        server_url="https://example.invalid",
        runtime_source=make_runtime(root, python),
    )


def test_invalid_native_runner_rejected_before_instance(tmp_path, delivery, fake_uv):
    root = tmp_path / "native-instance"
    with pytest.raises(InstallationError, match="shebang"):
        install_instance(
            delivery,
            root=root,
            instance_id="native",
            roles=["client"],
            server_url="https://example.invalid",
            runner_python="relative/python",
        )
    assert not root.exists()


def test_prepare_explicit_native_uv_ignores_path(tmp_path, delivery, fake_uv, monkeypatch):
    root = tmp_path / "instance"
    create(root, delivery)
    before = (root / "active.json").read_bytes()
    executable = tmp_path / "packaged-tools" / "uv"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 99\n")
    executable.chmod(0o700)
    monkeypatch.setattr(module.shutil, "which", lambda _: pytest.fail("PATH lookup not allowed"))
    fake_uv.clear()
    result = module._prepare(
        root, delivery, module.inspect_bundle(delivery), ["client"], uv_executable=executable
    )
    assert (root / "active.json").read_bytes() == before
    tool_calls = [args for args, _ in fake_uv if "--no-config" in args]
    assert tool_calls and all(args[0] == str(executable.resolve()) for args in tool_calls)
    assert (root / "releases" / result["release"] / "installation.json").is_file()
    for args, _ in fake_uv:
        if args[2] == "venv":
            assert args[-1] == str(root / "releases" / result["release"] / "client/venv")


@pytest.mark.parametrize("tool", ["relative/uv", "/missing-native-uv"])
def test_invalid_explicit_uv_refused_before_release(tmp_path, delivery, fake_uv, tool):
    root = tmp_path / "instance"
    create(root, delivery)
    before = set((root / "releases").iterdir())
    with pytest.raises(InstallationError, match="absolute executable"):
        module._prepare(
            root, delivery, module.inspect_bundle(delivery), ["client"], uv_executable=tool
        )
    assert set((root / "releases").iterdir()) == before


def test_linux_client_install_requests_native_extra(tmp_path, delivery, fake_uv, monkeypatch):
    """A Linux bundle's declared extras -- not the module constant -- drive uv."""
    linux = tmp_path / "linux-bundle"
    (tmp_path / "w").mkdir()
    wheels = {c: make_wheel(tmp_path / "w", c) for c in COMPONENTS}
    # The client extra must declare both exact core dependencies; rebuild the
    # device wheel with that metadata so the contract check is exercised.
    with zipfile.ZipFile(wheels["device"]) as archive:
        entries = {n: archive.read(n) for n in archive.namelist()}
    metadata = next(n for n in entries if n.endswith("/METADATA"))
    entries[metadata] += (
        b"Provides-Extra: client\n"
        b'Requires-Dist: ai-device-bash==0.2.0; extra == "client"\n'
        b'Requires-Dist: ai-device-fs==0.2.0; extra == "client"\n'
    )
    with zipfile.ZipFile(wheels["device"], "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    constraints = tmp_path / "linux-pins.txt"
    constraints.write_text("cryptography==50.0.2\n")
    build_bundle(
        linux,
        release_version="0.2.0",
        wheels=wheels,
        constraints=constraints,
        sources={c: "a" * 40 for c in COMPONENTS},
        target_system="linux",
    )
    monkeypatch.setattr(module.sys, "platform", "linux")
    create(tmp_path / "linux instance", linux)
    installs = [args for args, _ in fake_uv if "install" in args]
    assert len(installs) == 1
    device = next(arg for arg in installs[0] if "ai-device[" in arg)
    assert device.endswith("ai-device[client,native] @ " + next(
        arg.split(" @ ")[1] for arg in installs[0] if "ai-device[" in arg
    ))
    assert "client" in device and "native" in device


def _darwin_manifest(monkeypatch, macos="13.6", machine="arm64"):
    from device_runtime.dependencies import DARWIN_TARGET

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: machine)
    monkeypatch.setattr(module.platform, "mac_ver", lambda: (macos, ("", "", ""), ""))
    return {
        "target": {"system": "darwin", "requires_python": ">=3.13", "offline": False},
        "format_version": 3,
        "roles": {"client": {}},
        "dependencies": {"manifest": {"target": DARWIN_TARGET}},
    }


def test_darwin_format3_accepts_arm64_macos_above_floor(monkeypatch):
    manifest = _darwin_manifest(monkeypatch)
    module._check_target(manifest, ["client"])


def test_darwin_format3_rejects_x86_64_host(monkeypatch):
    manifest = _darwin_manifest(monkeypatch, machine="x86_64")
    with pytest.raises(InstallationError, match="arm64 macOS"):
        module._check_target(manifest, ["client"])


def test_darwin_format3_rejects_macos_below_floor(monkeypatch):
    manifest = _darwin_manifest(monkeypatch, macos="10.15")
    with pytest.raises(InstallationError, match="macOS >= 11.0"):
        module._check_target(manifest, ["client"])


def test_darwin_format3_rejects_unreadable_macos_version(monkeypatch):
    manifest = _darwin_manifest(monkeypatch, macos="")
    with pytest.raises(InstallationError, match="macOS >= 11.0"):
        module._check_target(manifest, ["client"])


def test_darwin_format3_rejects_invalid_dependency_target(monkeypatch):
    manifest = _darwin_manifest(monkeypatch)
    manifest["dependencies"]["manifest"]["target"] = {
        "system": "darwin",
        "machine": "arm64",
        "python": "cp313",
        "min_macos": "10.9",
    }
    with pytest.raises(InstallationError, match="invalid local dependency target"):
        module._check_target(manifest, ["client"])
