"""Build a ready-to-run native client tree from already verified offline inputs.

Run only in the disposable target-platform builder. No service operations.
Input hashes are checked against the supplied repository locks before extraction.
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tarfile
import zipfile
from pathlib import Path

# Native executables this tree ships, relative to the tree root. MUST match
# NATIVE_IDENTITY_FILES in src/device_runtime/version.py: readiness compares the
# two digests exactly, so a divergence makes every upgrade fail to become ready.
NATIVE_IDENTITY_FILES = ("bin/ai-device-client-launcher", "bin/ai-device-client-commit")

# The C sources those executables -- and the installer -- are compiled from.
# Shipped inside the tree so the running process can recompute the same digest
# from files it can actually reach: the runtime fingerprint must be independent
# of client-identity.json, or readiness would only be comparing a file with
# itself. MUST match NATIVE_IDENTITY_SOURCES in version.py.
NATIVE_IDENTITY_SOURCES = "native"
NATIVE_IDENTITY_SUFFIXES = (".h", ".c", ".m")


def verify(path, record):
    if (
        path.stat().st_size != record["size"]
        or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
    ):
        raise ValueError(f"locked input mismatch: {path.name}")


def verify_installed_wheels(wheels, site_packages):
    records = []
    for wheel in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if name.endswith(".py") and ".data/" not in name:
                    installed = site_packages / name
                    if not installed.is_file() or installed.read_bytes() != archive.read(name):
                        raise ValueError(
                            f"installed source differs from wheel: {wheel.name}:{name}"
                        )
        records.append(
            {"name": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
        )
    if not records:
        raise ValueError("no application wheels")
    return records


def relocate_python_scripts(runtime):
    """The Linux package has a fixed prefix; never retain builder shebangs."""
    for path in (runtime / "bin").iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            first = stream.readline(4096)
        if first.startswith(b"#!") and b"python" in first.lower():
            content = path.read_bytes()
            path.write_bytes(
                b"#!/opt/ai-device-client/runtime/bin/python3\n" + content.split(b"\n", 1)[1]
            )


def build_target():
    host = (platform.system(), platform.machine())
    if host == ("Linux", "x86_64"):
        return "linux-x86_64", "linux-amd64", "tool-lock.json", "dependency-lock.json"
    if host == ("Darwin", "arm64"):
        return (
            "darwin-arm64",
            "darwin-arm64",
            "tool-lock-darwin-arm64.json",
            "dependency-lock-darwin-arm64.json",
        )
    raise ValueError("build requires native Linux amd64 or macOS arm64")


def stage_native_sources(output):
    """Copy the native build inputs into the tree next to the built binaries.

    These are the sources the two shipped executables and the upgrade entry are
    compiled from. They go into the tree because the runtime fingerprint has to
    be recomputable from the installed tree alone: if it read
    client-identity.json instead, readiness would compare a digest with the file
    that recorded it, which proves nothing.

    The upgrade entry itself still lives outside the tree (it performs the swap
    and cannot be replaced by it), so its own build inputs are covered here as
    source rather than as a compiled artifact -- see write_client_identity.
    """
    native = Path(__file__).resolve().parents[1] / "native"
    destination = output / NATIVE_IDENTITY_SOURCES
    if destination.exists():
        raise ValueError("native source directory would be overwritten")
    destination.mkdir(parents=True)
    staged = 0
    for path in sorted(native.iterdir()):
        if path.suffix not in NATIVE_IDENTITY_SUFFIXES:
            continue
        if not path.is_file() or path.is_symlink():
            raise ValueError("native source must be regular files")
        (destination / path.name).write_bytes(path.read_bytes())
        staged += 1
    if not staged:
        raise ValueError("no native sources staged into the client tree")


def write_client_identity(output):
    """Match version.py's runtime fingerprint without importing target code.

    Inputs, mirroring device_runtime/version.py exactly:
      1. every .py in the installed package;
      2. every native executable this tree ships (NATIVE_IDENTITY_FILES),
         by tree-relative name and byte content;
      3. the native sources those binaries and the upgrade entry are built from
         (NATIVE_IDENTITY_SOURCES), by tree-relative name and bytes.

    The native binaries must be built before this runs; build() orders it that
    way. Readiness compares this digest against the runtime's own, so a drift
    between the two implementations is not cosmetic -- it makes every upgrade
    fail to become ready. tests/test_client_tree_build.py pins them together.

    A missing native binary is hashed as empty rather than raising: the digest
    then differs from any real build, which is a truthful "this tree is not
    complete" rather than a silent pass.
    """
    import ast
    import re

    package = output / "runtime/lib/python3.13/site-packages/device_runtime"
    syntax = ast.parse((package / "__init__.py").read_text())
    versions = [
        node.value.value
        for node in syntax.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(versions) != 1 or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,64}", versions[0]):
        raise ValueError("client source version is ambiguous or invalid")
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("client source must be regular files")
        name, content = path.relative_to(package).as_posix().encode(), path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    for relative in NATIVE_IDENTITY_FILES:
        marker = relative.encode()
        digest.update(len(marker).to_bytes(8, "big") + marker)
        binary = output / relative
        content = binary.read_bytes() if binary.is_file() else None
        # MUST match version.py: presence is part of the identity, so an absent
        # executable cannot hash the same as one that is present but empty.
        if content is None:
            digest.update(b"\x00")
        else:
            digest.update(b"\x01" + len(content).to_bytes(8, "big") + content)
    sources = output / NATIVE_IDENTITY_SOURCES
    # MUST match version.py: a tree with no native sources directory (Linux) is
    # distinct from one that has an empty one.
    if sources.is_dir():
        digest.update(b"\x01")
        for path in sorted(sources.iterdir()):
            if path.suffix not in NATIVE_IDENTITY_SUFFIXES:
                continue
            if not path.is_file():
                raise ValueError("staged native source is not a regular file")
            name = path.relative_to(output).as_posix().encode()
            content = path.read_bytes()
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(len(content).to_bytes(8, "big") + content)
    else:
        digest.update(b"\x00")
    identity = {"version": versions[0], "build_id": "sha256:" + digest.hexdigest()}
    with (output / "client-identity.json").open("x") as stream:
        stream.write(json.dumps(identity, separators=(",", ":")) + "\n")
    return identity


def build_macos_commit_entry(output):
    """Compile the standalone commit executable used by the package postinstall.

    Separate from client-macos-installer.m on purpose: that binary embeds a
    resource-table.h generated from the finished tree, so it cannot be produced
    as part of the tree it describes. This entry only renames one directory and
    needs no inventory.
    """
    source = Path(__file__).resolve().parents[1] / "native/client-macos-commit-main.c"
    target = output / "bin/ai-device-client-commit"
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
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
            str(Path(__file__).resolve().parents[1] / "native"),
            str(source),
            "-o",
            str(target),
        ],
        check=True,
    )
    subprocess.run(["/usr/bin/lipo", "-verify_arch", "arm64", str(target)], check=True)
    target.chmod(0o755)


def build_macos_launcher(output):
    """Compile the fixed managed entry; never execute or register it here."""
    source = Path(__file__).resolve().parents[1] / "native/client-macos-launcher.c"
    target = output / "bin/ai-device-client-launcher"
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
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
            str(source),
            "-o",
            str(target),
        ],
        check=True,
    )
    subprocess.run(["/usr/bin/lipo", "-verify_arch", "arm64", str(target)], check=True)
    target.chmod(0o755)


def build(inputs, locks, output):
    target, manifest_platform, tool_lock, dependency_lock = build_target()
    runtime = json.loads((locks / "runtime-lock.json").read_text())["targets"][target]
    tools = json.loads((locks / tool_lock).read_text())
    dependencies = json.loads((locks / dependency_lock).read_text())
    verify(inputs / "python.tar.gz", runtime)
    verify(inputs / "uv.whl", tools)
    for item in dependencies["wheels"]:
        verify(inputs / "dependencies" / item["path"], item)
    output.mkdir(parents=True, exist_ok=False)
    with tarfile.open(inputs / "python.tar.gz") as archive:
        archive.extractall(output, filter="data")
    (output / "python").rename(output / "runtime")
    python = output / "runtime/bin/python3"
    # The tool is a build input, never part of the installed runtime.
    uv_path = inputs / "build-uv"
    with zipfile.ZipFile(inputs / "uv.whl") as archive:
        names = [n for n in archive.namelist() if n.endswith("/scripts/uv")]
        if len(names) != 1:
            raise ValueError("uv executable missing from locked wheel")
        with uv_path.open("xb") as stream:
            stream.write(archive.read(names[0]))
    uv_path.chmod(0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("UV_", "PYTHON"))}
    env["UV_OFFLINE"] = "1"
    try:
        subprocess.run(
            [
                str(uv_path),
                "--no-config",
                "--no-cache",
                "pip",
                "install",
                "--python",
                str(python),
                "--no-index",
                "--find-links",
                str(inputs / "wheels"),
                "--find-links",
                str(inputs / "dependencies"),
                "ai-device[client]",
            ],
            env=env,
            check=True,
        )
        subprocess.run(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                (
                    "from device_runtime.executor import LocalExecutor; "
                    "from device_runtime.client_cli import main; "
                    "import device_runtime.client_package, device_runtime.client_register; "
                    "import mcp_server_bash.core, mcp_server_fs.core"
                ),
            ],
            env=env,
            cwd="/",
            check=True,
        )
        # Query the actual target runtime, including packages bundled with
        # CPython, rather than claiming the requested dependency set is complete.
        inventory = subprocess.run(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                (
                    "import importlib.metadata as m,json; "
                    "print(json.dumps(sorted([{'name':d.metadata['Name'],'version':d.version,"
                    "'license_files':[str(p) for p in (d.files or []) if any(s in str(p).lower() "
                    "for s in ('license','licence','copying','notice'))]} for d in m.distributions()],"
                    "key=lambda d:d['name'].lower())))"
                ),
            ],
            env=env,
            cwd="/",
            check=True,
            capture_output=True,
            text=True,
        )
        (output / "third-party-inventory.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "scope": "installed Python distributions; not a legal compliance audit",
                    "license_base": "runtime/lib/python3.13/site-packages",
                    "python_license": "runtime/lib/python3.13/LICENSE.txt",
                    "distributions": json.loads(inventory.stdout),
                },
                indent=2,
            )
            + "\n"
        )
        relocate_python_scripts(output / "runtime")
        if target == "darwin-arm64":
            build_macos_launcher(output)
            build_macos_commit_entry(output)
            stage_native_sources(output)
        records = verify_installed_wheels(
            inputs / "wheels", output / "runtime/lib/python3.13/site-packages"
        )
        write_client_identity(output)
        (output / "build-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "platform": manifest_platform,
                    "application_wheels": records,
                    "runtime_sha256": runtime["sha256"],
                    "dependency_lock_sha256": hashlib.sha256(
                        (locks / dependency_lock).read_bytes()
                    ).hexdigest(),
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        uv_path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--locks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.inputs.resolve(), args.locks.resolve(), args.output.resolve())
