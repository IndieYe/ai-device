"""New-instance native payload installation via the existing release core.

The package-owned bootstrap invokes this module with its own CPython and uv.
No service operations or implicit adoption of existing instances are permitted.
"""

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from .bundle import inspect_bundle
from .installation import InstallationError, install_instance


def _check_bundle_copy_space(bundle, manifest, destination, runtime=None):
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
    if runtime is not None:
        # The instance also gets its own interpreter copy; count it here so a
        # tight filesystem is rejected before anything is written.
        for item in Path(runtime).rglob("*"):
            if item.is_file() and not item.is_symlink():
                files.append(item)
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
    _check_bundle_copy_space(payload / "bundle", manifest, root, payload / "runtime")
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
            runtime_source=payload / "runtime",
        )
        return {**result, "service_started": False, "paired": False, "bootstrap": str(python)}
    finally:
        if previous is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = previous


def _fork_as(uid, gid, work):
    """Run ``work`` in a child dropped to ``uid``/``gid`` and return its result.

    Instance creation must be done by the account that will own the tree, while
    unit registration and service start must be done by root. Only the child
    changes identity, so the parent keeps the privileges the later steps need.
    """
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # child
        try:
            os.close(read_fd)
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            if os.geteuid() != uid or os.geteuid() == 0:
                raise InstallationError("privilege drop failed")
            payload = json.dumps(work(), sort_keys=True).encode()
            with os.fdopen(write_fd, "wb") as stream:
                stream.write(payload)
            os._exit(0)
        except BaseException as exc:  # noqa: BLE001 - report, never leak a traceback
            try:
                with os.fdopen(write_fd, "wb") as stream:
                    stream.write(
                        json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode()
                    )
            except BaseException:  # noqa: BLE001, S110 - already failing
                pass
            os._exit(1)

    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as stream:
        raw = stream.read()
    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)
    if code != 0:
        detail = ""
        if raw:
            try:
                detail = json.loads(raw).get("error", "")
            except ValueError:
                detail = raw.decode("utf-8", "replace")
        raise InstallationError(f"owner step failed: {detail or f'exit {code}'}")
    if not raw:
        raise InstallationError("owner step returned no result")
    return json.loads(raw)


def provision(
    *,
    payload,
    root,
    instance_id,
    server_url,
    username,
    unit_dir,
    environment_file,
    registry=None,
    start=False,
):
    """Create the instance as its owner, then register and optionally start it.

    This is the single-command path: the caller runs it as root and does not
    have to sequence the two identities itself. Each underlying step keeps its
    own checks; nothing here weakens them.
    """
    import pwd

    from . import native_helper as helper

    if sys.platform != "linux" or os.geteuid() != 0:
        raise InstallationError("provisioning requires administrator privileges")

    entry = pwd.getpwnam(username)
    if entry.pw_uid == 0:
        raise InstallationError("the instance account must be non-root")

    root = Path(root).absolute()
    created = _fork_as(
        entry.pw_uid,
        entry.pw_gid,
        lambda: install_payload(
            payload, root=root, instance_id=instance_id, server_url=server_url
        ),
    )

    registered = helper.register(
        helper._path(registry) if registry else helper.REGISTRY,
        Path(unit_dir),
        instance_id=instance_id,
        root=root,
        username=username,
        environment_file=Path(environment_file),
    )

    started = False
    if start:
        unit = f"ai-device-client-{instance_id}.service"
        env = {"PATH": "/usr/bin:/bin", "LANG": "C"}
        for command in (["daemon-reload"], ["enable", unit], ["start", unit]):
            result = subprocess.run(
                ["/usr/bin/systemctl", *command],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            if result.returncode != 0:
                raise InstallationError(
                    f"systemctl {' '.join(command)} failed: {result.stderr.strip()}"
                )
        started = True

    return {
        **created,
        "registered": registered,
        "service_started": started,
        "paired": False,
    }


def main():
    parser = argparse.ArgumentParser(description="Install a new offline native Device instance")
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--server-url", required=True)
    # Single-command provisioning. Without --register this behaves exactly as
    # before: create the instance as the invoking (non-root) user and stop.
    parser.add_argument("--username", default=None, help="instance account; enables --register")
    parser.add_argument("--unit-dir", type=Path, default=Path("/etc/systemd/system"))
    parser.add_argument("--environment-file", type=Path, default=None)
    parser.add_argument("--registry", type=Path, default=None)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()

    try:
        if args.register or args.start:
            if not args.register:
                raise InstallationError("--start requires --register")
            if not args.username or not args.environment_file:
                raise InstallationError(
                    "--register requires --username and --environment-file"
                )
            result = provision(
                payload=args.payload,
                root=args.root,
                instance_id=args.instance_id,
                server_url=args.server_url,
                username=args.username,
                unit_dir=args.unit_dir,
                environment_file=args.environment_file,
                registry=args.registry,
                start=args.start,
            )
        else:
            result = install_payload(
                args.payload,
                root=args.root,
                instance_id=args.instance_id,
                server_url=args.server_url,
            )
    except (InstallationError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"native installation refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
