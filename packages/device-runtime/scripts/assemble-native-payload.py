"""Assemble an offline Linux payload from explicit local inputs; never download.

Run with the build workspace Python. Runtime/uv archives are separately pinned
and verified by the caller's build lock; output is not a signed release.
"""

import argparse
import hashlib
import json
import shutil
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile

from device_runtime.bundle import build_bundle
from device_runtime.dependencies import TARGET


def assemble(output, wheels, dependencies, constraints, uv_wheel, source_commit):
    tool_lock = json.loads((Path(__file__).parents[1] / "native/tool-lock.json").read_text())
    if (
        uv_wheel.name != tool_lock["filename"]
        or uv_wheel.stat().st_size != tool_lock["size"]
        or hashlib.sha256(uv_wheel.read_bytes()).hexdigest() != tool_lock["sha256"]
    ):
        raise ValueError("packaged uv does not match native tool lock")
    if output.exists():
        raise ValueError("output already exists")
    output.mkdir(parents=True)
    try:
        entries = []
        # Preserve input wheel bytes and derive redistributor metadata, not placeholders.
        for path in sorted(dependencies.glob("*.whl")):
            with ZipFile(path) as archive:
                member = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
                metadata = BytesParser().parsebytes(archive.read(member))
                license_name = metadata.get("License-Expression") or metadata.get("License")
                if not license_name:
                    license_name = "; ".join(
                        item
                        for item in metadata.get_all("Classifier", [])
                        if item.startswith("License ::")
                    )
                if not license_name:
                    raise ValueError(f"missing license metadata: {path.name}")
                name, version = metadata["Name"], metadata["Version"]
            entries.append(
                {
                    "path": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "source": f"https://pypi.org/project/{name}/{version}/",
                    "license": license_name,
                }
            )
        if not entries:
            raise ValueError("no dependency wheels")
        dependency_copy = output / ".dependencies"
        shutil.copytree(dependencies, dependency_copy)
        (dependency_copy / "manifest.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "target": TARGET,
                    "wheels": entries,
                },
                indent=2,
            )
            + "\n"
        )
        components = {}
        for component, prefix in [
            ("device", "ai_device-"),
            ("bash", "ai_device_bash-"),
            ("fs", "ai_device_fs-"),
        ]:
            matches = list(wheels.glob(prefix + "*.whl"))
            if len(matches) != 1:
                raise ValueError(f"expected exactly one {component} wheel")
            components[component] = matches[0]
        from packaging.utils import parse_wheel_filename

        release_version = str(parse_wheel_filename(components["device"].name)[1])
        build_bundle(
            output / "bundle",
            release_version=release_version,
            wheels=components,
            constraints=constraints,
            sources={c: source_commit for c in components},
            target_system="linux",
            dependency_dir=dependency_copy,
        )
        shutil.rmtree(dependency_copy)
        tools = output / "tools"
        tools.mkdir()
        with ZipFile(uv_wheel) as archive:
            members = [n for n in archive.namelist() if n.endswith(".data/scripts/uv")]
            if len(members) != 1:
                raise ValueError("expected one packaged uv executable")
            (tools / "uv").write_bytes(archive.read(members[0]))
        (tools / "uv").chmod(0o755)
        (output / "build-inputs.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_commit": source_commit,
                    "uv_wheel": uv_wheel.name,
                    "uv_sha256": hashlib.sha256(uv_wheel.read_bytes()).hexdigest(),
                    "development_build": True,
                    "component_sha256": {
                        c: hashlib.sha256(p.read_bytes()).hexdigest() for c, p in components.items()
                    },
                    "provenance_note": "source_commit is the base; exact wheel bytes define this unsigned development build",
                },
                indent=2,
            )
            + "\n"
        )
    except BaseException:
        # Output was exclusively created by this invocation, never an existing instance.
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "wheels", "dependencies", "constraints", "uv-wheel"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    assemble(
        args.output,
        args.wheels,
        args.dependencies,
        args.constraints,
        args.uv_wheel,
        args.source_commit,
    )


if __name__ == "__main__":
    main()
