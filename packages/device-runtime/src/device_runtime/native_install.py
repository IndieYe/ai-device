"""New-instance native payload installation via the existing release core.

The package-owned bootstrap invokes this module with its own CPython and uv.
No service operations or implicit adoption of existing instances are permitted.
"""

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path

from .bundle import inspect_bundle
from .installation import InstallationError, install_instance


def _check_bundle_copy_space(bundle, manifest, destination):
    """Reject only when the verified bundle's copied files cannot fit.

    This is a lower bound, not a reservation or an estimate of the venv,
    filesystem overhead, quotas, or concurrent use.
    """
    files = [
        bundle / "manifest.json",
        bundle / "constraints.txt",
        bundle / "dependencies/manifest.json",
    ]
    files.extend(bundle / entry["path"] for entry in manifest["components"].values())
    files.extend(
        bundle / "dependencies" / entry["path"]
        for entry in manifest["dependencies"]["manifest"]["wheels"]
    )
    try:
        required = sum(path.stat().st_size for path in files)
        parent = destination.parent
        while True:
            try:
                info = parent.stat()
                break
            except FileNotFoundError:
                if parent.is_symlink() or parent == parent.parent:
                    raise
                parent = parent.parent
        if not stat.S_ISDIR(info.st_mode):
            raise OSError("destination ancestor is not a directory")
        free = shutil.disk_usage(parent).free
    except OSError as exc:
        raise InstallationError("cannot determine available space for bundle copy") from exc
    if free < required:
        raise InstallationError("insufficient space for bundle copy")


def install_payload(payload, *, root, instance_id, server_url):
    if os.geteuid() == 0:
        raise InstallationError(
            "native client instances must be created by the selected non-root user"
        )
    payload = Path(payload).resolve()
    python = payload / "runtime/bin/python3.13"
    uv = payload / "tools/uv"
    if Path(sys.executable).resolve() != python.resolve():
        raise InstallationError("native installation must run with its packaged interpreter")
    if not uv.is_file() or not os.access(uv, os.X_OK):
        raise InstallationError("packaged uv is missing or not executable")
    manifest = inspect_bundle(payload / "bundle")
    if manifest["format_version"] != 3 or manifest["target"]["system"] != "linux":
        raise InstallationError("native installation requires a complete offline Linux bundle")
    root = Path(root).absolute()
    if root.exists() or root.is_symlink():
        raise InstallationError("native install refuses existing instances; adoption is explicit")
    if "\n" in str(python) or " " in str(python) or len(str(python).encode()) > 120:
        raise InstallationError("packaged interpreter path cannot be used as a stable shebang")
    _check_bundle_copy_space(payload / "bundle", manifest, root)
    # Child processes inherit only the packaged tool PATH. Restore caller state.
    previous = os.environ.get("PATH")
    os.environ["PATH"] = str(payload / "tools") + ":/usr/bin:/bin"
    try:
        result = install_instance(
            payload / "bundle",
            root=root,
            instance_id=instance_id,
            roles=["client"],
            server_url=server_url,
            runner_python=python,
        )
        return {**result, "service_started": False, "paired": False, "bootstrap": str(python)}
    finally:
        if previous is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = previous


def main():
    parser = argparse.ArgumentParser(description="Install a new offline native Device instance")
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--server-url", required=True)
    args = parser.parse_args()
    try:
        result = install_payload(
            args.payload,
            root=args.root,
            instance_id=args.instance_id,
            server_url=args.server_url,
        )
    except (InstallationError, OSError, ValueError) as exc:
        parser.exit(1, f"native installation refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
