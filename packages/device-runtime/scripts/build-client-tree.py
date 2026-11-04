"""Build a ready-to-run Linux client tree from already verified offline inputs.

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


def build(inputs, locks, output):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("build inside the Linux amd64 builder")
    runtime = json.loads((locks / "runtime-lock.json").read_text())["targets"]["linux-x86_64"]
    tools = json.loads((locks / "tool-lock.json").read_text())
    dependencies = json.loads((locks / "dependency-lock.json").read_text())
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
        records = verify_installed_wheels(
            inputs / "wheels", output / "runtime/lib/python3.13/site-packages"
        )
        (output / "build-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "platform": "linux-amd64",
                    "application_wheels": records,
                    "runtime_sha256": runtime["sha256"],
                    "dependency_lock_sha256": hashlib.sha256(
                        (locks / "dependency-lock.json").read_bytes()
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
