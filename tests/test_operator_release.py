"""Private profile fixtures only; no real operator or network access."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load("operator_runtime", ROOT / "operator/runtime.py")
manifest_tool = load("operator_manifest", ROOT / "scripts/operator_manifest.py")


def private(path, data):
    path.write_text(json.dumps(data))
    path.chmod(0o600)


@pytest.fixture
def profile(tmp_path):
    root = tmp_path / "profile"
    release = root / "releases/0.2.2-abc123"
    release.mkdir(parents=True)
    for role in ("mcp", "ops"):
        python = release / role / "venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexit 0\n")
        python.chmod(0o700)
    manifest = {
        "schema_version": 1,
        "release_id": release.name,
        "roles": {
            role: {"python": f"{role}/venv/bin/python"} for role in ("mcp", "ops")
        },
    }
    private(release / "release.json", manifest)
    private(
        root / "active-release.json",
        {
            "schema_version": 1,
            "release_id": release.name,
            "manifest_sha256": hashlib.sha256(
                (release / "release.json").read_bytes()
            ).hexdigest(),
        },
    )
    private(root / "config.json", {"server_url": runtime.CENTER})
    private(root / "credentials.json", {"admin_token": "fixture-only-token-not-real"})
    return root


@pytest.fixture(autouse=True)
def real_parser_for_unit_fixtures(monkeypatch):
    original = runtime.classify_ops
    monkeypatch.setattr(
        runtime,
        "classify_ops",
        lambda python, args, env: original(sys.executable, args, env),
    )


def test_both_roles_use_one_binding(profile):
    for role in ("mcp", "ops"):
        assert (
            runtime.resolve(profile, role)
            == profile / f"releases/0.2.2-abc123/{role}/venv/bin/python"
        )


@pytest.mark.parametrize(
    "change", ["missing", "digest", "traversal", "mode", "symlink", "interpreter"]
)
def test_bad_binding_fails_closed(profile, change):
    path = profile / "active-release.json"
    data = json.loads(path.read_text())
    if change == "missing":
        path.unlink()
    elif change == "mode":
        path.chmod(0o644)
    elif change == "symlink":
        original = profile / "saved.json"
        path.rename(original)
        path.symlink_to(original)
    elif change == "interpreter":
        runtime.resolve(profile, "mcp").unlink()
    else:
        data["manifest_sha256" if change == "digest" else "release_id"] = "../outside"
        private(path, data)
    with pytest.raises((OSError, ValueError)):
        runtime.resolve(profile, "mcp")


@pytest.mark.parametrize(
    "role,args", [("mcp", []), ("ops", ["version"]), ("ops", ["--help"])]
)
def test_exec_contract(profile, monkeypatch, role, args):
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "DEVICE_RUNTIME_ADMIN_TOKEN",
        "DEVICE_RUNTIME_API_URL",
        "DEVICE_RUNTIME_TOKEN",
        "DEVICE_RUNTIME_DEVICE_TOKENS",
    ):
        monkeypatch.setenv(key, "poison")
    captured = []
    monkeypatch.setattr(runtime.os, "execve", lambda *args: captured.append(args))
    runtime.run(profile, role, args)
    python, argv, env = captured[0]
    assert argv[:3] == [python, "-I", "-m"]
    assert "PYTHONPATH" not in env and "DEVICE_RUNTIME_TOKEN" not in env
    if "--help" in args:
        assert "DEVICE_RUNTIME_ADMIN_TOKEN" not in env
        assert "DEVICE_RUNTIME_API_URL" not in env
    else:
        assert env["DEVICE_RUNTIME_API_URL"] == runtime.CENTER
        assert env["DEVICE_RUNTIME_ADMIN_TOKEN"] == "fixture-only-token-not-real"


@pytest.mark.parametrize(
    "option", ["--server-url=x", "--server", "--token-env", "--token"]
)
def test_override_rejected(profile, option):
    with pytest.raises(ValueError, match="pinned"):
        runtime.run(
            profile,
            "ops",
            [option, "x", "version"] if "=" not in option else [option, "version"],
        )


def test_remote_argv_not_parsed_as_connection_flags(profile, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime.os, "execve", lambda *args: calls.append(args))
    runtime.run(
        profile,
        "ops",
        [
            "run",
            "--device-id",
            "fixture",
            "--operation-id",
            "op",
            "--cwd",
            "/tmp",
            "--",
            "echo",
            "--server-url=payload",
        ],
    )
    assert calls[0][1][-1] == "--server-url=payload"


@pytest.mark.parametrize("change", ["url", "token", "mode", "json"])
def test_private_profile_error_redacted(profile, change, capsys):
    path = profile / "credentials.json"
    if change == "url":
        private(
            profile / "config.json", {"server_url": "https://not-the-center.invalid"}
        )
    elif change == "token":
        private(path, {"admin_token": "short"})
    elif change == "mode":
        path.chmod(0o644)
    else:
        path.write_text("fixture-only-sensitive-malformed-json")
    assert runtime.main(profile, "ops", ["version"]) == 2
    output = capsys.readouterr().err
    assert "fixture-only" not in output and "short" not in output
    assert json.loads(output)["code"] == "operator_entry_unavailable"


def test_real_launch_subprocess_without_network(profile):
    import shutil

    for name in ("runtime.py", "launch.py"):
        shutil.copyfile(ROOT / "operator" / name, profile / name)
    python = runtime.resolve(profile, "mcp")
    python.write_text(
        f"#!{sys.executable}\nimport json,sys,os\nif '-c' in sys.argv: os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\nprint(json.dumps(sys.argv[1:]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-B", str(profile / "launch.py"), "mcp"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == ["-I", "-m", "device_runtime.mcp_server"]


def test_real_ops_entry_in_fake_home(profile, tmp_path):
    import shutil

    home = tmp_path / "home"
    target = home / ".device-runtime/operators/public-center"
    target.parent.mkdir(parents=True)
    shutil.copytree(profile, target)
    shutil.copyfile(ROOT / "operator/runtime.py", target / "runtime.py")
    python = runtime.resolve(target, "ops")
    python.write_text(
        f"#!{sys.executable}\nimport json,sys,os\nif '-c' in sys.argv: os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\nprint(json.dumps(sys.argv[1:]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "operator/device-ops"), "--help"],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == [
        "-I",
        "-m",
        "device_runtime.cli",
        "ops",
        "--help",
    ]


def test_manifest_records_inventory_and_requires_wheels(profile, monkeypatch):
    release = profile / "releases/0.2.2-abc123"
    inventory = {
        "python_version": "3.13.12",
        "platform": "darwin",
        "machine": "arm64",
        "runtime_files": {
            "device_runtime/__init__.py": hashlib.sha256(b"fixture").hexdigest()
        },
        "packages": [{"name": "ai-device", "version": "0.2.2"}],
    }
    calls = []

    def probe(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(inventory))

    monkeypatch.setattr(manifest_tool.subprocess, "run", probe)
    with pytest.raises(ValueError, match="wheel"):
        manifest_tool.build(release)
    (release / "wheels").mkdir()
    with zipfile.ZipFile(release / "wheels/device.whl", "w") as wheel:
        wheel.writestr(
            "ai_device-0.2.2.dist-info/METADATA", "Name: ai-device\nVersion: 0.2.2\n"
        )
        wheel.writestr("device_runtime/__init__.py", b"fixture")
    data = manifest_tool.build(release)
    assert data["roles"]["mcp"]["python_version"] == "3.13.12"
    assert (
        data["wheels"][0]["sha256"]
        == hashlib.sha256((release / "wheels/device.whl").read_bytes()).hexdigest()
    )
    assert calls[0][1:4] == ["-I", "-B", "-c"]


def test_manifest_publication_is_no_clobber(tmp_path):
    manifest_tool.save(tmp_path, {"schema_version": 1})
    with pytest.raises(FileExistsError):
        manifest_tool.save(tmp_path, {"schema_version": 2})
    assert json.loads((tmp_path / "release.json").read_text()) == {"schema_version": 1}
    assert not list(tmp_path.glob(".release-*"))


def test_manifest_real_probe_no_installs(tmp_path):
    """Editable installations must fail explicitly; wheel layouts have inventory."""
    import importlib.metadata

    package = Path(
        importlib.metadata.distribution("ai-device").locate_file("device_runtime")
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", manifest_tool.PROBE],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if not package.is_dir():
        assert result.returncode != 0
        assert "non-wheel package layout" in result.stderr
    else:
        assert result.returncode == 0
        assert json.loads(result.stdout)["runtime_files"]


@pytest.mark.parametrize(
    "change", ["role-version", "wheel-version", "source", "extra-wheel"]
)
def test_manifest_rejects_inconsistent_device(tmp_path, monkeypatch, change):
    release = tmp_path / "release-0.2.2"
    (release / "wheels").mkdir(parents=True)
    source = b"fixture"
    version = "0.2.3" if change == "wheel-version" else "0.2.2"
    with zipfile.ZipFile(release / "wheels/device.whl", "w") as wheel:
        wheel.writestr(
            "ai_device.dist-info/METADATA", f"Name: ai-device\nVersion: {version}\n"
        )
        wheel.writestr("device_runtime/__init__.py", source)
    if change == "extra-wheel":
        import shutil

        shutil.copyfile(release / "wheels/device.whl", release / "wheels/duplicate.whl")

    def probe(argv, **kwargs):
        installed_version = (
            "0.2.3" if change == "role-version" and "/ops/" in argv[0] else "0.2.2"
        )
        digest = hashlib.sha256(
            b"modified" if change == "source" else source
        ).hexdigest()
        data = {
            "packages": [{"name": "ai-device", "version": installed_version}],
            "runtime_files": {"device_runtime/__init__.py": digest},
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(data))

    monkeypatch.setattr(manifest_tool.subprocess, "run", probe)
    with pytest.raises(ValueError):
        manifest_tool.build(release)
    assert not (release / "release.json").exists()


@pytest.mark.parametrize(
    "suffix",
    [
        ["echo", "--help"],
        ["echo", "--server-url=x"],
        ["echo", "--token-env=x"],
        ["--", "echo", "--help"],
    ],
)
def test_remote_remainder_injects_profile(profile, monkeypatch, suffix):
    calls = []
    monkeypatch.setattr(runtime.os, "execve", lambda *args: calls.append(args))
    args = [
        "run",
        "--device-id",
        "fixture",
        "--operation-id",
        "fixture-op",
        "--cwd",
        "/tmp",
        *suffix,
    ]
    runtime.run(profile, "ops", args)
    assert calls[0][1][-len(args) :] == args
    assert calls[0][2]["DEVICE_RUNTIME_API_URL"] == runtime.CENTER
    assert calls[0][2]["DEVICE_RUNTIME_ADMIN_TOKEN"] == "fixture-only-token-not-real"


@pytest.mark.parametrize(
    "args", [["--help"], ["run", "--help"], ["--server-url=x", "--help"]]
)
def test_actual_local_help_needs_no_credentials(profile, monkeypatch, args):
    (profile / "credentials.json").unlink()
    calls = []
    monkeypatch.setattr(runtime.os, "execve", lambda *args: calls.append(args))
    runtime.run(profile, "ops", args)
    assert "DEVICE_RUNTIME_ADMIN_TOKEN" not in calls[0][2]


def test_real_unrecorded_source_is_rejected(tmp_path):
    release = tmp_path / "release"
    (release / "wheels").mkdir(parents=True)
    with zipfile.ZipFile(release / "wheels/device.whl", "w") as wheel:
        wheel.writestr(
            "ai_device.dist-info/METADATA", "Name: ai-device\nVersion: 0.2.2\n"
        )
        wheel.writestr("device_runtime/__init__.py", b"fixture=1")
    for role in ("mcp", "ops"):
        env = release / role / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(env)], check=True
        )
        site = Path(
            subprocess.check_output(
                [
                    str(env / "bin/python"),
                    "-I",
                    "-c",
                    "import sysconfig;print(sysconfig.get_path('purelib'))",
                ],
                text=True,
            ).strip()
        )
        package = site / "device_runtime"
        package.mkdir()
        (package / "__init__.py").write_bytes(b"fixture=1")
        (package / "unrecorded.py").write_text("VALUE=42")
        metadata = site / "ai_device-0.2.2.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Name: ai-device\nVersion: 0.2.2\n")
        (metadata / "RECORD").write_text("device_runtime/__init__.py,,\n")
    with pytest.raises(ValueError, match="differs"):
        manifest_tool.build(release)
    for role in ("mcp", "ops"):
        env = release / role / "venv"
        site = Path(
            subprocess.check_output(
                [
                    str(env / "bin/python"),
                    "-I",
                    "-c",
                    "import sysconfig;print(sysconfig.get_path('purelib'))",
                ],
                text=True,
            ).strip()
        )
        (site / "device_runtime/unrecorded.py").unlink()
    assert len(manifest_tool.build(release)["roles"]["mcp"]["runtime_files"]) == 1
