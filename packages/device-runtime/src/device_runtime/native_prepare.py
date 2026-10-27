"""Ordinary-owner native candidate preparation. Never activates or installs packages.

The current CPython and explicit uv must live at trusted administrator-owned,
non-writable persistent paths. Extracted candidate tools are never executed.
"""

import argparse
import fcntl
import hashlib
import os
import sys
from pathlib import Path

from . import installation as core
from . import native_candidate as candidate
from . import native_helper as helper
from .management_lock import MARKER, native_preparation_lease
from .protocol import canonical


class PreparationError(ValueError):
    pass


def _binding(root):
    if sys.platform != "linux" or os.geteuid() == 0 or root.stat().st_uid != os.geteuid():
        raise PreparationError("registered Linux instance owner required; not root")
    marker_path = root / MARKER
    helper._trusted_marker(marker_path)
    marker = candidate._json(marker_path.read_bytes())
    binding = marker.get("binding", {})
    if (
        marker.get("schema_version") != 2
        or binding.get("root") != str(root)
        or binding.get("uid") != os.geteuid()
    ):
        raise PreparationError("complete native registration binding required")
    for relative, key in (
        ("run", "runner_sha256"),
        ("instance.json", "identity_sha256"),
        ("config/roles.json", "roles_sha256"),
    ):
        if helper._digest(root / relative) != binding.get(key):
            raise PreparationError("native instance binding changed")
    identity = candidate._json((root / "instance.json").read_bytes())
    if identity.get("instance_id") != binding.get("instance_id"):
        raise PreparationError("instance identity differs")
    helper._trusted(Path(binding["unit_path"]))
    if helper._digest(Path(binding["unit_path"])) != binding.get("unit_sha256"):
        raise PreparationError("registered unit changed")
    active = candidate._json((root / "active.json").read_bytes())
    if active.get("roles") != ["client"]:
        raise PreparationError("single client instance required")
    return binding, active


def _durable_tree(root):
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    directories = [p for p in root.rglob("*") if p.is_dir() and not p.is_symlink()]
    for path in sorted(directories, key=lambda p: len(p.parts), reverse=True) + [root]:
        with helper._directory_fd(path) as fd:
            os.fsync(fd)


def prepare_runtime_candidate(
    root,
    package,
    envelope,
    *,
    trusted_keys,
    release_id,
    operation_id,
    bootstrap_version,
    uv_executable,
):
    """Reauthenticate and prepare in final release directory, with no implicit retry.

    A matching completed ID returns its receipt after verifying source and
    release metadata. Any intent without completion stays blocked, even if its
    process has gone: this does not claim subprocess recovery is implemented.
    """
    root = Path(root).resolve()
    helper._identifier(operation_id)
    binding, active = _binding(root)
    uv = Path(uv_executable)
    if not uv.is_absolute() or not uv.is_file() or not os.access(uv, os.X_OK):
        raise PreparationError("explicit persistent uv executable required")
    helper._trusted(uv.resolve())
    helper._trusted(Path(sys.executable).resolve())
    envelope = Path(envelope)
    if envelope.stat().st_size > 256000:
        raise PreparationError("release envelope too large")
    signed = candidate._json(envelope.read_bytes())
    payload = candidate.verify_release(
        signed,
        trusted_keys,
        release_id=release_id,
        target="linux-amd64-deb",
        source_version=active["release_version"],
        bootstrap_version=bootstrap_version,
    )
    request = {
        "release_sha256": hashlib.sha256(canonical(payload).encode()).hexdigest(),
        "source_active_sha256": helper._digest(root / "active.json"),
        "binding_sha256": helper._digest(root / MARKER),
        "python": str(Path(sys.executable).resolve()),
        "python_sha256": helper._digest(Path(sys.executable).resolve()),
        "uv": str(uv.resolve()),
        "uv_sha256": helper._digest(uv),
    }
    # Dedicated native entry explicitly validates the schema2 marker above;
    # the legacy default remains forbidden. EX management excludes all other
    # maintenance; SH installation allows the live client's runtime lease.
    with (
        native_preparation_lease(root),
        (root / ".installation.lock").open("rb") as lease,
    ):
        try:
            fcntl.flock(lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PreparationError("instance maintenance busy") from exc
        if (
            _binding(root) != (binding, active)
            or helper._digest(root / "active.json") != request["source_active_sha256"]
        ):
            raise PreparationError("source changed before native preparation")
        directory = root / "native-candidates"
        if directory.is_symlink():
            raise PreparationError("candidate journal directory must not be a symlink")
        directory.mkdir(mode=0o700, exist_ok=True)
        with helper._directory_fd(root) as fd:
            os.fsync(fd)
        stem = directory / operation_id
        intent = stem.with_suffix(".intent.json")
        result = stem.with_suffix(".result.json")
        if intent.exists():
            if candidate._json(intent.read_bytes()) != request:
                raise PreparationError("candidate operation ID conflict")
            if not result.exists():
                raise PreparationError("candidate preparation incomplete; no automatic replay")
            receipt = candidate._json(result.read_bytes())
            if receipt.get("state") != "runtime_prepared":
                raise PreparationError("candidate preparation failed; no automatic replay")
            release = receipt["candidate"]["release"]
            if Path(release).name != release or release in {".", ".."}:
                raise PreparationError("invalid prepared release path")
            home = root / "releases" / release
            python = home / "client/venv/bin/python"
            if not python.is_file() or not os.access(python, os.X_OK):
                raise PreparationError("prepared runtime interpreter unavailable")
            if (
                candidate._json((home / "installation.json").read_bytes()) != receipt["candidate"]
                or core._fingerprint(core.inspect_bundle(home / "bundle"))
                != receipt["candidate"]["bundle_sha256"]
            ):
                raise PreparationError("prepared runtime evidence changed")
            return receipt
        for pending in directory.glob("*.intent.json"):
            previous = pending.with_name(pending.name.replace(".intent.json", ".result.json"))
            if (
                not previous.exists()
                or candidate._json(previous.read_bytes()).get("state") != "runtime_prepared"
            ):
                raise PreparationError(
                    "incomplete or failed native preparation blocks new operation"
                )
        helper._save_new(intent, request)
        try:
            extracted = stem.with_suffix(".extracted")
            authentication = candidate.prepare_candidate(
                package,
                envelope,
                extracted,
                trusted_keys=trusted_keys,
                release_id=release_id,
                target="linux-amd64-deb",
                source_version=active["release_version"],
                bootstrap_version=bootstrap_version,
            )
            if authentication["release_sha256"] != request["release_sha256"]:
                raise PreparationError("release changed during preparation")
            bundle = extracted / "root" / authentication["payload_root"] / "bundle"
            manifest = core.inspect_bundle(bundle)
            prepared = core._prepare(root, bundle, manifest, ["client"], uv_executable=uv)
            home = root / "releases" / prepared["release"]
            _durable_tree(home)
            with helper._directory_fd(home.parent) as fd:
                os.fsync(fd)
            if (
                helper._digest(root / "active.json") != request["source_active_sha256"]
                or helper._digest(root / MARKER) != request["binding_sha256"]
            ):
                raise PreparationError("source changed during preparation; candidate not approved")
            receipt = {
                "state": "runtime_prepared",
                "request": request,
                "authentication": authentication,
                "candidate": prepared,
                "activated": False,
            }
            helper._save_new(result, receipt)
            return receipt
        except Exception:
            if not result.exists():
                helper._save_new(
                    result, {"state": "failed", "request": request, "activated": False}
                )
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "root",
        "package",
        "envelope",
        "trusted-keys",
        "release-id",
        "operation-id",
        "bootstrap-version",
        "uv-executable",
    ):
        parser.add_argument("--" + name, required=True)
    args = vars(parser.parse_args())
    args["trusted_keys"] = candidate._json(Path(args["trusted_keys"]).read_bytes())
    print(canonical(prepare_runtime_candidate(**args)))


if __name__ == "__main__":
    main()
