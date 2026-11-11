"""Build a NON-INSTALLABLE no-payload resource inspection PKG on macOS arm64.

No installer/service execution. A successful build does not authorize installing
this package or prove lifecycle safety. The preinstall script always refuses.
"""

import argparse
import hashlib
import importlib.util
import json
import platform
import re
import shutil
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "mac_resource_inventory", Path(__file__).with_name("client-macos-resource-manifest.py")
)
INVENTORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INVENTORY)
IDENTIFIER = "local.ai-device.client.nopayload-inspection"
REFUSAL = (
    "#!/bin/sh\nprintf 'Offline resource inspection only; installation refused.\\n' >&2\nexit 1\n"
)


def compile_resource_check(manifest, scripts, build_directory, *, run=None):
    """Embed exact build inventory; executable accepts no runtime tree argument."""
    build_directory.mkdir()
    records = []
    for entry in manifest["entries"]:
        kind = {"file": 0, "directory": 1, "symlink": 2}[entry["kind"]]
        value = entry.get("sha256", entry.get("target", ""))
        records.append(
            "{{{},{},{},{},{}}}".format(
                json.dumps(entry["path"]),
                kind,
                entry.get("mode", 0),
                entry.get("size", 0),
                json.dumps(value),
            )
        )
    (build_directory / "resource-table.h").write_text(
        "static const struct resource resources[] = {\n"
        + ",\n".join(records)
        + "\n};\n#define RESOURCE_COUNT (sizeof(resources)/sizeof(resources[0]))\n"
    )
    execute = run or subprocess.run
    execute(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-arch",
            "arm64",
            "-mmacosx-version-min=11.0",
            "-I",
            str(build_directory),
            str(Path(__file__).parents[1] / "native/client-macos-resource-check.c"),
            "-o",
            str(scripts / "verify-resources"),
        ],
        check=True,
    )
    (scripts / "verify-resources").chmod(0o755)


def compile_installer_inspection(scripts, build_directory, *, run=None):
    """Compile only; refusal preinstall never invokes this administrator entry."""
    execute = run or subprocess.run
    execute(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-arch",
            "arm64",
            "-mmacosx-version-min=11.0",
            "-fobjc-arc",
            "-framework",
            "Foundation",
            "-I",
            str(build_directory),
            str(Path(__file__).parents[1] / "native/client-macos-installer.m"),
            "-o",
            str(scripts / "installer-inspection"),
        ],
        check=True,
    )
    (scripts / "installer-inspection").chmod(0o755)


def build(tree, output, version, *, run=None):
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise ValueError("native macOS arm64 builder required")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", version):
        raise ValueError("numeric inspection package version required")
    # Compare actual locations, including existing symlink parents and '..',
    # before creating anything: an output inside tree would copy itself.
    tree, output = Path(tree).resolve(), Path(output).resolve()
    if output.is_relative_to(tree) or tree.is_relative_to(output):
        raise ValueError("separate output directory required")
    source_manifest = json.loads((tree / "build-manifest.json").read_text())
    if (
        source_manifest.get("schema_version") != 1
        or source_manifest.get("platform") != "darwin-arm64"
    ):
        raise ValueError("darwin-arm64 tree required")
    # Build-time checks only, never run a supplied launcher or Python here.
    if not (tree / "runtime/bin/python3").is_file():
        raise ValueError("Python runtime missing")
    launcher = tree / "bin/ai-device-client-launcher"
    if launcher.is_symlink() or not launcher.is_file() or launcher.stat().st_mode & 0o111 != 0o111:
        raise ValueError("fixed launcher missing or not executable")
    if not (tree / "third-party-inventory.json").is_file():
        raise ValueError("third-party inventory missing")
    # Do not mutate, delete or overwrite an existing experiment.
    output.mkdir(parents=True, exist_ok=False)
    scripts = output / "scripts"
    resources = scripts / "resources"
    resources.mkdir(parents=True)
    staged = resources / "client-tree"
    shutil.copytree(
        tree, staged, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    manifest = INVENTORY.inventory(staged)
    raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    (resources / "tree-manifest.json").write_bytes(raw)
    compile_resource_check(manifest, scripts, output / "verifier-build", run=run)
    compile_installer_inspection(scripts, output / "verifier-build", run=run)
    identity = {
        "schema_version": 1,
        "purpose": "offline-inspection-only",
        "platform": "darwin-arm64",
        "resource_path": "resources/client-tree",
        "manifest_path": "resources/tree-manifest.json",
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "launcher_sha256": hashlib.sha256(
            (staged / "bin/ai-device-client-launcher").read_bytes()
        ).hexdigest(),
        "verifier_sha256": hashlib.sha256((scripts / "verify-resources").read_bytes()).hexdigest(),
        "installer_sha256": hashlib.sha256(
            (scripts / "installer-inspection").read_bytes()
        ).hexdigest(),
        "package_version": version,
    }
    (resources / "inspection-identity.json").write_text(
        json.dumps(identity, sort_keys=True, indent=2) + "\n"
    )
    preinstall = scripts / "preinstall"
    preinstall.write_text(REFUSAL)
    preinstall.chmod(0o755)
    package = output / f"ai-device-client-{version}-arm64-nopayload-inspection.pkg"
    execute = run or subprocess.run
    execute(
        [
            "/usr/bin/pkgbuild",
            "--nopayload",
            "--scripts",
            str(scripts),
            "--identifier",
            IDENTIFIER,
            "--version",
            version,
            str(package),
        ],
        check=True,
    )
    expanded = output / "expanded"
    execute(["/usr/sbin/pkgutil", "--expand-full", str(package), str(expanded)], check=True)
    if (expanded / "Payload").exists():
        raise ValueError("unexpected system payload")
    actual = expanded / "Scripts"
    if (actual / "verify-resources").read_bytes() != (scripts / "verify-resources").read_bytes():
        raise ValueError("native verifier changed")
    if (actual / "installer-inspection").read_bytes() != (
        scripts / "installer-inspection"
    ).read_bytes():
        raise ValueError("native inspection installer changed")
    if (actual / "preinstall").read_text() != REFUSAL:
        raise ValueError("installation refusal gate changed")
    for name in ("tree-manifest.json", "inspection-identity.json"):
        if (actual / "resources" / name).read_bytes() != (resources / name).read_bytes():
            raise ValueError("expanded resource identity mismatch")
    INVENTORY.verify(actual / "resources/client-tree", manifest)
    return package


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(build(args.tree, args.output, args.version))
