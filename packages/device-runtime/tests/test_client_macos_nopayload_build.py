import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
spec = importlib.util.spec_from_file_location("nopayload_inspection", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    root = tmp_path / "tree"
    (root / "runtime/bin").mkdir(parents=True)
    (root / "runtime/bin/python3").write_bytes(b"fixture Python, never executed")
    (root / "bin").mkdir()
    launcher = root / "bin/ai-device-client-launcher"
    launcher.write_bytes(b"fixture native, never executed")
    launcher.chmod(0o755)
    (root / "build-manifest.json").write_text(
        json.dumps({"schema_version": 1, "platform": "darwin-arm64"})
    )
    (root / "third-party-inventory.json").write_text("{}")
    return root


def runner(output, calls, mutate=None):
    def run(argv, *, check):
        assert check
        calls.append(argv)
        if argv[0] == "/usr/bin/xcrun":
            Path(argv[-1]).write_bytes(b"fixture verifier")
        elif argv[0] == "/usr/bin/pkgbuild":
            assert "--nopayload" in argv and "--root" not in argv
            Path(argv[-1]).write_bytes(b"fixture PKG")
        else:
            assert argv[:2] == ["/usr/sbin/pkgutil", "--expand-full"]
            expanded = Path(argv[-1])
            expanded.mkdir()
            shutil.copytree(output / "scripts", expanded / "Scripts", symlinks=True)
            if mutate:
                mutate(expanded)
        return subprocess.CompletedProcess(argv, 0)

    return run


def test_build_fixed_resources_and_refusal(tree, tmp_path):
    output = tmp_path / "package"
    calls = []
    result = module.build(tree, output, "0.3.4.4", run=runner(output, calls))
    assert result.is_file()
    assert len(calls) == 4
    identity = json.loads((output / "scripts/resources/inspection-identity.json").read_text())
    assert identity["purpose"] == "offline-inspection-only"
    assert identity["resource_path"] == "resources/client-tree"
    assert "exit 1" in (output / "scripts/preinstall").read_text()


@pytest.mark.parametrize("mutation", ["payload", "gate", "identity", "file"])
def test_expanded_mismatch_is_failure(tree, tmp_path, mutation):
    output = tmp_path / "package"

    def mutate(root):
        if mutation == "payload":
            (root / "Payload").mkdir()
        elif mutation == "gate":
            (root / "Scripts/preinstall").write_text("exit 0")
        elif mutation == "identity":
            (root / "Scripts/resources/inspection-identity.json").write_text("{}")
        else:
            (root / "Scripts/resources/client-tree/bin/ai-device-client-launcher").write_bytes(
                b"wrong"
            )

    with pytest.raises(ValueError):
        module.build(tree, output, "0.3.4.4", run=runner(output, [], mutate))


@pytest.mark.parametrize("fault", ["platform", "missing-launcher", "bad-version", "overlap"])
def test_invalid_input_never_calls_packager(tree, tmp_path, monkeypatch, fault):
    version = "0.3.4.4"
    output = tmp_path / "output"
    if fault == "platform":
        monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    elif fault == "missing-launcher":
        (tree / "bin/ai-device-client-launcher").unlink()
    elif fault == "bad-version":
        version = "invalid"
    else:
        output = tree / "package"
    with pytest.raises(ValueError):
        module.build(tree, output, version, run=lambda *a, **kw: pytest.fail("no packager"))
    assert not output.exists()


@pytest.mark.skipif(
    (__import__("platform").system(), __import__("platform").machine()) != ("Darwin", "arm64"),
    reason="native macOS verifier",
)
@pytest.mark.parametrize(
    "mutation", ["none", "content", "mode", "extra", "missing", "link", "argument"]
)
def test_real_native_verifier_bound_to_sibling_resources(tree, tmp_path, mutation):
    scripts = tmp_path / "scripts"
    resource = scripts / "resources/client-tree"
    resource.parent.mkdir(parents=True)
    shutil.copytree(tree, resource)
    (resource / "alias").symlink_to("third-party-inventory.json")
    inventory = module.INVENTORY.inventory(resource)
    module.compile_resource_check(inventory, scripts, tmp_path / "compile")
    if mutation == "content":
        (resource / "third-party-inventory.json").write_text('{"modified":true}')
    elif mutation == "mode":
        (resource / "third-party-inventory.json").chmod(0o755)
    elif mutation == "extra":
        (resource / "extra").write_text("not in manifest")
    elif mutation == "missing":
        (resource / "third-party-inventory.json").unlink()
    elif mutation == "link":
        (resource / "alias").unlink()
        (resource / "alias").symlink_to("/etc/hosts")
    args = [str(scripts / "verify-resources")]
    if mutation == "argument":
        args.append(str(tree))
    result = subprocess.run(args, capture_output=True, check=False, timeout=10)
    assert result.returncode == (0 if mutation == "none" else 64 if mutation == "argument" else 70)
    assert (b"RESOURCE_VERIFIED_NOT_INSTALLED" in result.stdout) == (mutation == "none")


@pytest.mark.parametrize("path_kind", ["symlink-parent", "parent-components"])
def test_resolved_output_overlap_rejected_before_source_mutation(
    tree, tmp_path, monkeypatch, path_kind
):
    if path_kind == "symlink-parent":
        alias = tmp_path / "tree-alias"
        alias.symlink_to(tree, target_is_directory=True)
        output = alias / "package"
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        output = outside / ".." / tree.name / "package"
    before = sorted(str(path.relative_to(tree)) for path in tree.rglob("*"))
    calls = []

    def unexpected_copy(*args, **kwargs):
        raise AssertionError("overlapping output reached source copy")

    monkeypatch.setattr(module.shutil, "copytree", unexpected_copy)
    with pytest.raises(ValueError, match="separate output directory required"):
        module.build(tree, output, "0.3.4.8", run=runner(output, calls))
    assert not calls
    assert sorted(str(path.relative_to(tree)) for path in tree.rglob("*")) == before
    assert not (tree / "package").exists()


def test_separate_symlink_parent_output_and_existing_directory_preserved(tree, tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir()
    alias = tmp_path / "output-alias"
    alias.symlink_to(destination, target_is_directory=True)
    output = alias / "package"
    calls = []
    module.build(tree, output, "0.3.4.8", run=runner(output, calls))
    assert calls
    marker = output / "keep"
    marker.write_text("existing experiment")
    repeated_calls = []
    with pytest.raises(FileExistsError):
        module.build(tree, output, "0.3.4.8", run=runner(output, repeated_calls))
    assert not repeated_calls
    assert marker.read_text() == "existing experiment"


def test_changed_native_verifier_in_expansion_rejected(tree, tmp_path):
    output = tmp_path / "package"

    def mutate(root):
        (root / "Scripts/verify-resources").write_bytes(b"different verifier")

    with pytest.raises(ValueError, match="native verifier changed"):
        module.build(tree, output, "0.3.4.6", run=runner(output, [], mutate))


def test_compile_failure_never_calls_pkgbuild(tree, tmp_path):
    calls = []

    def fail(argv, *, check):
        calls.append(argv)
        raise subprocess.CalledProcessError(1, argv)

    with pytest.raises(subprocess.CalledProcessError):
        module.build(tree, tmp_path / "failed", "0.3.4.6", run=fail)
    assert len(calls) == 1 and calls[0][0] == "/usr/bin/xcrun"


def test_inspection_installer_compile_and_expansion_gate(tree, tmp_path):
    output = tmp_path / "package"
    calls = []

    def mutate(expanded):
        (expanded / "Scripts/installer-inspection").write_bytes(b"changed")

    with pytest.raises(ValueError, match="native inspection installer changed"):
        module.build(tree, output, "0.3.4.9", run=runner(output, calls, mutate))
    compiler = [argv for argv in calls if argv[0] == "/usr/bin/xcrun"]
    assert len(compiler) == 2
    assert compiler[1][-3].endswith("native/client-macos-installer.m")
    assert "-fobjc-arc" in compiler[1] and "Foundation" in compiler[1]
    assert (output / "scripts/preinstall").read_text() == module.REFUSAL
    identity = json.loads((output / "scripts/resources/inspection-identity.json").read_text())
    import hashlib

    assert identity["installer_sha256"] == hashlib.sha256(b"fixture verifier").hexdigest()


def test_inspection_installer_compile_failure_stops_before_pkgbuild(tree, tmp_path):
    output = tmp_path / "failed-installer"
    calls = []
    base = runner(output, calls)

    def fail(argv, *, check):
        if argv[0] == "/usr/bin/xcrun" and argv[-1].endswith("installer-inspection"):
            calls.append(argv)
            raise subprocess.CalledProcessError(1, argv)
        return base(argv, check=check)

    with pytest.raises(subprocess.CalledProcessError):
        module.build(tree, output, "0.3.4.9", run=fail)
    assert len(calls) == 2
    assert all(argv[0] == "/usr/bin/xcrun" for argv in calls)
