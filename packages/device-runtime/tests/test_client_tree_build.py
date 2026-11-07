import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/build-client-tree.py"
spec = importlib.util.spec_from_file_location("client_tree_build", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_exact_application_source_verification(tmp_path):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = wheels / "application.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("example/__init__.py", "VERSION = 2\n")
    site = tmp_path / "site"
    (site / "example").mkdir(parents=True)
    installed = site / "example/__init__.py"
    installed.write_text("VERSION = 1\n")
    with pytest.raises(ValueError, match="differs"):
        module.verify_installed_wheels(wheels, site)
    installed.write_text("VERSION = 2\n")
    records = module.verify_installed_wheels(wheels, site)
    assert records == [
        {"name": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
    ]


def test_input_hash_mismatch(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="mismatch"):
        module.verify(path, {"size": 5, "sha256": "0" * 64})


def test_relocate_python_scripts_preserves_mode_and_binary(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    script = binaries / "device-runtime"
    script.write_bytes(b"#!/tmp/build/runtime/bin/python3\nprint('ok')\n")
    script.chmod(0o755)
    binary = binaries / "python3"
    binary.write_bytes(b"\x7fELFpython")
    link = binaries / "python"
    link.symlink_to("python3")
    module.relocate_python_scripts(tmp_path)
    assert script.read_bytes() == b"#!/opt/ai-device-client/runtime/bin/python3\nprint('ok')\n"
    assert script.stat().st_mode & 0o777 == 0o755
    assert binary.read_bytes() == b"\x7fELFpython"
    assert link.is_symlink()


@pytest.mark.parametrize(
    "system,machine,target",
    [("Linux", "x86_64", "linux-x86_64"), ("Darwin", "arm64", "darwin-arm64")],
)
def test_build_target_chooses_native_locks(monkeypatch, system, machine, target):
    monkeypatch.setattr(module.platform, "system", lambda: system)
    monkeypatch.setattr(module.platform, "machine", lambda: machine)
    selected, _, tool, dependencies = module.build_target()
    assert selected == target
    if target == "darwin-arm64":
        assert tool == "tool-lock-darwin-arm64.json"
        assert dependencies == "dependency-lock-darwin-arm64.json"
    else:
        assert tool == "tool-lock.json" and dependencies == "dependency-lock.json"


@pytest.mark.parametrize(
    "system,machine", [("Darwin", "x86_64"), ("Linux", "aarch64"), ("Windows", "ARM64")]
)
def test_build_target_rejects_unsupported_hosts(monkeypatch, system, machine):
    monkeypatch.setattr(module.platform, "system", lambda: system)
    monkeypatch.setattr(module.platform, "machine", lambda: machine)
    with pytest.raises(ValueError, match="native"):
        module.build_target()


def test_macos_launcher_build_contract(tmp_path, monkeypatch):
    calls = []

    def run(argv, *, check):
        assert check
        calls.append(argv)
        if argv[0] == "/usr/bin/xcrun":
            Path(argv[-1]).write_bytes(b"test Mach-O placeholder")

    monkeypatch.setattr(module.subprocess, "run", run)
    module.build_macos_launcher(tmp_path)
    binary = tmp_path / "bin/ai-device-client-launcher"
    assert calls[0][-3] == str(SCRIPT.parents[1] / "native/client-macos-launcher.c")
    assert "-arch" in calls[0] and "arm64" in calls[0]
    assert not any(arg.startswith("-D") for arg in calls[0])
    assert calls[1] == ["/usr/bin/lipo", "-verify_arch", "arm64", str(binary)]
    assert binary.stat().st_mode & 0o777 == 0o755


def test_macos_launcher_build_failure_propagates(tmp_path, monkeypatch):
    import subprocess

    def fail(argv, *, check):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(module.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        module.build_macos_launcher(tmp_path)


@pytest.mark.skipif(
    (module.platform.system(), module.platform.machine()) != ("Darwin", "arm64"),
    reason="macOS arm64 compiler",
)
def test_build_real_fixed_launcher_without_executing(tmp_path):
    module.build_macos_launcher(tmp_path)
    binary = tmp_path / "bin/ai-device-client-launcher"
    assert binary.is_file()
    assert binary.stat().st_mode & 0o777 == 0o755


def test_client_identity_matches_actual_runtime_fingerprint(tmp_path):
    """The two fingerprints must agree, including the native binaries.

    Readiness compares client-identity.json against what the running client
    reports for itself, so a drift between build-client-tree.py and
    version.py does not degrade a diagnostic -- it makes every upgrade fail
    to become ready. The native executables are created here specifically so
    this test covers the part that used to be missing.
    """
    import subprocess
    import sys

    package = tmp_path / "runtime/lib/python3.13/site-packages/device_runtime"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.3.4"\n')
    (package / "nested").mkdir()
    (package / "nested/tool.py").write_text("value = 1\n")
    (tmp_path / "bin").mkdir()
    for name, content in (
        ("ai-device-client-launcher", b"launcher-bytes"),
        ("ai-device-client-commit", b"commit-bytes"),
    ):
        (tmp_path / "bin" / name).write_bytes(content)
    identity = module.write_client_identity(tmp_path)
    version_source = Path(__file__).parents[1] / "src/device_runtime/version.py"
    code = """import ast, pathlib, sys
source=ast.parse(pathlib.Path(sys.argv[1]).read_text())
native=next(n for n in source.body if isinstance(n,ast.Assign) and any(getattr(t,"id",None)=="NATIVE_IDENTITY_FILES" for t in n.targets))
function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=="_source_fingerprint")
ns={"Path":pathlib.Path,"__file__":sys.argv[2],"hashlib":__import__("hashlib")}
exec(compile(ast.Module(body=[native,function],type_ignores=[]),"fingerprint","exec"),ns)
print(ns["_source_fingerprint"]())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(version_source), str(package / "version.py")],
        check=True,
        capture_output=True,
        text=True,
    )
    assert identity == {"version": "0.3.4", "build_id": result.stdout.strip()}
    assert json.loads((tmp_path / "client-identity.json").read_text()) == identity
    with pytest.raises(FileExistsError):
        module.write_client_identity(tmp_path)


@pytest.mark.parametrize(
    "initializer",
    ['__version__="bad version"', "__version__=1", '__version__="1"\n__version__="2"', ""],
)
def test_client_identity_rejects_invalid_version(tmp_path, initializer):
    package = tmp_path / "runtime/lib/python3.13/site-packages/device_runtime"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(initializer)
    with pytest.raises(ValueError):
        module.write_client_identity(tmp_path)
    assert not (tmp_path / "client-identity.json").exists()


def _identity_tree(tmp_path, launcher=b"launcher-bytes", commit=b"commit-bytes"):
    package = tmp_path / "runtime/lib/python3.13/site-packages/device_runtime"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.3.4"\n')
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/ai-device-client-launcher").write_bytes(launcher)
    (tmp_path / "bin/ai-device-client-commit").write_bytes(commit)
    return tmp_path


def test_build_id_moves_when_a_native_binary_changes(tmp_path):
    """A native-only change must not leave build_id untouched.

    This is the exact hole this test was added for: a fix confined to
    native/*.h compiled into a different installer binary while build_id stayed
    identical, so the identity claimed two different builds were the same one.
    """
    before = module.write_client_identity(_identity_tree(tmp_path / "a"))["build_id"]
    after = module.write_client_identity(
        _identity_tree(tmp_path / "b", launcher=b"launcher-bytes-v2")
    )["build_id"]
    assert before != after


@pytest.mark.parametrize("which", ["launcher", "commit"])
def test_build_id_is_sensitive_to_each_native_binary(tmp_path, which):
    changed = {"ai-device-client-launcher": b"launcher-bytes", "ai-device-client-commit": b"commit-bytes"}
    changed[f"ai-device-client-{which}"] = b"different"
    before = module.write_client_identity(_identity_tree(tmp_path / "base"))["build_id"]
    after = module.write_client_identity(
        _identity_tree(
            tmp_path / "mod",
            launcher=changed["ai-device-client-launcher"],
            commit=changed["ai-device-client-commit"],
        )
    )["build_id"]
    assert before != after


def test_missing_native_binary_changes_the_digest(tmp_path):
    """Absent must never collide with present-but-empty.

    A tree missing its launcher is not a valid install, and its identity must
    not equal a tree that has one, or the two would be indistinguishable.
    """
    present = module.write_client_identity(_identity_tree(tmp_path / "present"))["build_id"]
    partial = _identity_tree(tmp_path / "partial")
    (partial / "bin/ai-device-client-launcher").unlink()
    absent = module.write_client_identity(partial)["build_id"]
    assert present != absent


def test_native_name_change_is_not_invisible(tmp_path):
    """Renaming a binary changes the identity even if all bytes survive."""
    before = module.write_client_identity(_identity_tree(tmp_path / "norm"))["build_id"]
    renamed = _identity_tree(tmp_path / "renamed")
    (renamed / "bin/ai-device-client-launcher").rename(renamed / "bin/other")
    (renamed / "bin/other").write_bytes(b"launcher-bytes")
    after = module.write_client_identity(renamed)["build_id"]
    assert before != after
