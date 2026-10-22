import fcntl
import json
import os
import shutil
import subprocess
import sys
import zipfile

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


def create(root, delivery, roles=("client",)):
    return install_instance(
        delivery,
        root=root,
        instance_id="isolated-mac",
        roles=roles,
        server_url="http://127.0.0.1:28765",
        port=28765,
    )


def persistent(root):
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
        and (p.parts[len(root.parts)] in ("config", "state") or p.name in ("instance.json", "run"))
    }


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
    result = subprocess.run(
        [sys.executable, str(root / "run"), "mcp"], capture_output=True, text=True, check=False
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
    process = subprocess.Popen(
        [sys.executable, str(root / "run"), "center"],
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
        f"entry = {str(root / 'run')!r}\n"
        "exec(compile(Path(entry).read_text(),entry,'exec'),{'__file__':entry})\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", injection, "center"],
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
    result = subprocess.run(
        [sys.executable, str(root / "run"), "client"],
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
