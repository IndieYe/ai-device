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
from .native_prepare_sender import PreparationSender, observe_senders
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


def _retired(stem, request):
    path = stem.with_suffix(".retired.json")
    if not path.exists():
        return False
    helper._trusted_marker(path)
    receipt = candidate._json(path.read_bytes())
    if receipt != {
        "state": "retired_without_replay",
        "request": request,
        "intent_sha256": helper._digest(stem.with_suffix(".intent.json")),
        "retry_requires_new_id": True,
    }:
        raise PreparationError("invalid administrator retirement evidence")
    return True


def retire_preparation(root, operation_id):
    """Explicit administrator retirement; retains all artifacts, never reruns.

    EX management proves the original preparation caller no longer owns its
    lease. Full subreaper receipts plus a free sender lock prove its commands
    have ended. Old direct-child evidence cannot qualify.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        raise PreparationError("Linux administrator required")
    root = helper._path(root)
    helper._identifier(operation_id)
    marker = root / MARKER
    helper._trusted_marker(marker)
    registration = candidate._json(marker.read_bytes())
    binding = registration.get("binding", {})
    if (
        registration.get("schema_version") != 2
        or binding.get("root") != str(root)
        or binding.get("uid") != root.stat().st_uid
        or root.stat().st_uid <= 0
    ):
        raise PreparationError("matching native owner required")
    with native_preparation_lease(root), (root / ".installation.lock").open("rb") as lease:
        fcntl.flock(lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
        stem = root / "native-candidates" / operation_id
        intent_path = stem.with_suffix(".intent.json")
        if intent_path.is_symlink() or intent_path.stat().st_size > 256000:
            raise PreparationError("invalid preparation intent")
        request = candidate._json(intent_path.read_bytes())
        if request.get("binding_sha256") != helper._digest(marker) or request.get(
            "source_active_sha256"
        ) != helper._digest(root / "active.json"):
            raise PreparationError("retirement source or binding changed")
        if _retired(stem, request):
            return candidate._json(stem.with_suffix(".retired.json").read_bytes())
        result = stem.with_suffix(".result.json")
        if result.exists():
            value = candidate._json(result.read_bytes())
            if value.get("state") != "failed" or value.get("request") != request:
                raise PreparationError("only failed or incomplete preparations may retire")
        observation = observe_senders(stem.with_suffix(".senders"), request)
        if (
            observation["state"] != "process_trees_reaped"
            or not observation["descendants_confirmed"]
        ):
            raise PreparationError("complete process-tree reaping required; remains blocked")
        receipt = {
            "state": "retired_without_replay",
            "request": request,
            "intent_sha256": helper._digest(intent_path),
            "retry_requires_new_id": True,
        }
        helper._save_new(stem.with_suffix(".retired.json"), receipt, mode=0o644)
        return receipt


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
                previous_request = candidate._json(pending.read_bytes())
                previous_stem = pending.with_name(pending.name.removesuffix(".intent.json"))
                if not _retired(previous_stem, previous_request):
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
            with (
                PreparationSender(
                    stem.with_suffix(".senders"), request, reap_descendants=True
                ) as sender,
                core.preparation_sender(sender),
            ):
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
    if len(sys.argv) > 1 and sys.argv[1] == "retire":
        parser = argparse.ArgumentParser(
            description="Explicit administrator retirement; no deletion or replay"
        )
        parser.add_argument("retire")
        parser.add_argument("--root", required=True)
        parser.add_argument("--operation-id", required=True)
        args = parser.parse_args()
        print(canonical(retire_preparation(args.root, args.operation_id)))
        return
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
