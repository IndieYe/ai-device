"""Ordinary-owner native candidate preparation. Never activates or installs packages.

The current CPython and explicit uv must live at trusted administrator-owned,
non-writable persistent paths. Extracted candidate tools are never executed.
"""

import argparse
import fcntl
import hashlib
import os
import stat
import sys
import uuid
from pathlib import Path

from . import installation as core
from . import lifecycle
from . import native_candidate as candidate
from . import native_helper as helper
from .management_lock import MARKER, native_preparation_lease
from .native_prepare_sender import PreparationSender, observe_senders
from .protocol import canonical


class PreparationError(ValueError):
    pass


# The signing key set has one administrator-owned home. Passing a path per call
# made the trust root a property of the caller, which is the same as having no
# trust root at all. --trusted-keys remains for provisioning and tests, but it
# must point at a tree the instance owner cannot write.
TRUSTED_KEYS = Path("/etc/ai-device-native/trusted-keys.json")


def _read_regular(path, error):
    """Read instance-owned evidence only when it is a regular file.

    These paths sit in the owner's tree, and a FIFO there would block this
    process with no error. Validate the type before any byte is read.
    """
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise PreparationError(error)
    return Path(path).read_bytes()


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
    identity = candidate._json(
        _read_regular(
            root / "instance.json", "instance identity must be a regular file"
        )
    )
    if identity.get("instance_id") != binding.get("instance_id"):
        raise PreparationError("instance identity differs")
    helper._trusted(Path(binding["unit_path"]))
    if helper._digest(Path(binding["unit_path"])) != binding.get("unit_sha256"):
        raise PreparationError("registered unit changed")
    active = candidate._json(
        _read_regular(root / "active.json", "active pointer must be a regular file")
    )
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


def _load_trusted_keys(path=None):
    """Load the signing key set from a path the instance owner cannot write.

    The key set is the root of every authenticity decision here, so it gets the
    same treatment as the interpreter and uv: administrator-owned, non-writable,
    no symlink anywhere in the ancestry. Reading an arbitrary path would let the
    owner mint its own key and satisfy every later check honestly.
    """
    keys_path = Path(path) if path is not None else TRUSTED_KEYS
    helper._trusted(keys_path.resolve())
    return candidate._json(_read_regular(keys_path, "trusted keys must be a regular file"))


def _activation_journal(root):
    return root / "native-activation.json"


def _read_pointer(root):
    """Read the single-line pointer, or None when it is absent."""
    path = root / "current"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise PreparationError("release pointer must be a regular file")
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def reconcile_activation(root):
    """Report whether the pointer and active.json still describe one release.

    _publish moves the pointer before committing active.json, so a crash between
    them leaves the launcher naming a release that the metadata has not accepted
    yet. run-runner refuses that state, which is correct -- but without a way to
    see it, the instance simply never starts again. This is read-only.
    """
    root = helper._path(root)
    active = _binding(root)[1]
    pointer = _read_pointer(root)
    journal = _activation_journal(root)
    pending = journal.is_file()
    if pointer is None:
        state = "pointer_missing"
    elif pointer == active["release"]:
        state = "consistent"
    else:
        state = "pointer_ahead"
    return {
        "state": state,
        "pointer": pointer,
        "active": active["release"],
        "journal": candidate._json(journal.read_bytes()) if pending else None,
        "pending": pending,
    }


def resolve_activation(root, release):
    """Finish or abandon a pointer/active mismatch with an explicit choice.

    ``release`` must be one of the two values already on disk; this never picks
    a third release and never runs on a live instance. Choosing the pointer's
    release completes the promotion the crashed run started; choosing the active
    release rolls the pointer back to what the metadata still describes.
    """
    root = helper._path(root)
    binding, active = _binding(root)
    observation = reconcile_activation(root)
    if observation["state"] == "consistent":
        raise PreparationError("pointer and active release already agree")
    if observation["state"] == "pointer_missing":
        raise PreparationError("release pointer is missing; restore it explicitly")
    if release == active["release"]:
        target = active["release"]
    elif release == observation["pointer"]:
        target = observation["pointer"]
    else:
        raise PreparationError("requested release is neither the pointer nor active")
    with (
        native_preparation_lease(root),
        (root / ".installation.lock").open("rb") as lease,
    ):
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PreparationError("instance is running or maintenance is active") from exc
        if _binding(root) != (binding, active):
            raise PreparationError("source changed before resolution")
        if release == active["release"]:
            core._write_pointer(root, active["release"])
            _activation_journal(root).unlink(missing_ok=True)
            return {"state": "rolled_back", "release": active["release"]}
        checked = lifecycle._check_release(root, target, active["roles"])
        core._json(root / "active.json", {**checked, "activation_id": uuid.uuid4().hex})
        _activation_journal(root).unlink(missing_ok=True)
        return {"state": "completed", "release": target}


def activate_candidate(root, release, *, envelope, trusted_keys, stopped=False):
    """Promote a prepared release for a registered instance.

    The service must already be stopped. This takes the installation lease
    exclusively, which only succeeds when no launcher holds it shared, so the
    lock -- not the caller's flag -- is what proves the instance is idle.

    The candidate record cannot authorize anything: it lives in the owner's own
    tree, so an owner can write a self-consistent one without ever passing
    signature verification. The envelope and trusted keys are therefore
    re-verified here, and the manifest digest that verification produced is
    compared against the release directory this promotes.
    """
    if not stopped:
        raise PreparationError("explicit --stopped confirmation required; this never stops processes")
    root = helper._path(root)
    binding, active = _binding(root)
    keys = _load_trusted_keys(trusted_keys)
    signed = candidate._read_envelope(Path(envelope))
    payload = candidate.authenticate_envelope(signed, keys)
    # The path component must match before it is joined onto the root.
    expected = lifecycle._release_id(release)
    with (
        native_preparation_lease(root),
        (root / ".installation.lock").open("rb") as lease,
    ):
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PreparationError("instance is running or maintenance is active") from exc
        if _binding(root) != (binding, active):
            raise PreparationError("source changed before activation")
        directory = root / "releases" / expected
        if directory.is_symlink() or (directory / "candidate.json").is_symlink():
            raise PreparationError("invalid candidate directory")
        record = candidate._json((directory / "candidate.json").read_bytes())
        if record.get("format_version") != 1 or record.get("binding") != core._lifecycle_binding(
            root, active
        ):
            raise PreparationError("candidate was not prepared for this instance state")
        # Reuse lifecycle's validation and publication, but never its public entry:
        # activate_release takes install._lock, which routes through
        # management_lease and rejects any instance carrying the native marker.
        # This function already holds the native maintenance lease exclusively.
        prepared = lifecycle._read(directory / "candidate.json")
        checked = lifecycle._check_release(root, expected, active["roles"])
        if checked != prepared["candidate"]:
            raise PreparationError("candidate installation record changed")
        # Bind the authenticated payload to the bytes about to be promoted. The
        # bundle manifest covers every wheel, so a release tampered with after
        # preparation stops matching here even though its own record still does.
        package = payload.get("package")
        if not isinstance(package, dict):
            raise PreparationError("signed payload lacks a package descriptor")
        manifest_sha256 = package.get("manifest_sha256")
        if helper._digest(directory / "bundle" / "manifest.json") != manifest_sha256:
            raise PreparationError("release bundle does not match the signed manifest")
        # _publish writes the pointer before active.json. Record the intended
        # destination first so a crash in between is diagnosable instead of
        # leaving a launcher that names a release the metadata never accepted.
        journal = _activation_journal(root)
        helper._save_new(
            journal,
            {
                "format_version": 1,
                "release": checked["release"],
                "previous": active["release"],
                "manifest_sha256": manifest_sha256,
            },
        )
        promoted = lifecycle._publish(root, checked)
        journal.unlink(missing_ok=True)
        return {"state": "activated", **promoted}


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
    signed = candidate._read_envelope(envelope)
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
                # The prepared release must start on its own after activation, so it
                # inherits the live release's private runtime exactly like
                # prepare_upgrade does. Without this the new release has no
                # interpreter and the launcher refuses it after the pointer moves.
                prepared = core._prepare(
                    root,
                    bundle,
                    manifest,
                    ["client"],
                    uv_executable=uv,
                    runtime_source=core._release_runtime_source(root, active["release"]),
                )
            home = root / "releases" / prepared["release"]
            _durable_tree(home)
            with helper._directory_fd(home.parent) as fd:
                os.fsync(fd)
            if (
                helper._digest(root / "active.json") != request["source_active_sha256"]
                or helper._digest(root / MARKER) != request["binding_sha256"]
            ):
                raise PreparationError("source changed during preparation; candidate not approved")
            # Record the lifecycle candidate binding so the same release can later be
            # activated through lifecycle.activate_release. Without this file the
            # registered instance has no way to promote a prepared release: the
            # application transaction ends at package_applied_pending_activation.
            core._write_candidate_record(root, active, prepared)
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
    if len(sys.argv) > 1 and sys.argv[1] == "activate":
        parser = argparse.ArgumentParser(
            description="Promote a prepared release for a stopped registered instance"
        )
        parser.add_argument("activate")
        parser.add_argument("--root", required=True)
        parser.add_argument("--release", required=True)
        parser.add_argument("--envelope", required=True)
        parser.add_argument("--trusted-keys", default=None)
        parser.add_argument("--stopped", action="store_true", required=True)
        args = parser.parse_args()
        print(
            canonical(
                activate_candidate(
                    args.root,
                    args.release,
                    envelope=args.envelope,
                    trusted_keys=args.trusted_keys,
                    stopped=args.stopped,
                )
            )
        )
        return
    if len(sys.argv) > 1 and sys.argv[1] == "reconcile-activation":
        parser = argparse.ArgumentParser(
            description="Report whether the release pointer and active.json still agree"
        )
        parser.add_argument("reconcile-activation")
        parser.add_argument("--root", required=True)
        args = parser.parse_args()
        print(canonical(reconcile_activation(args.root)))
        return
    if len(sys.argv) > 1 and sys.argv[1] == "resolve-activation":
        parser = argparse.ArgumentParser(
            description="Finish or abandon a crashed activation; never stops processes"
        )
        parser.add_argument("resolve-activation")
        parser.add_argument("--root", required=True)
        parser.add_argument("--release", required=True)
        parser.add_argument("--stopped", action="store_true", required=True)
        args = parser.parse_args()
        if not args.stopped:
            raise PreparationError(
                "explicit --stopped confirmation required; this never stops processes"
            )
        print(canonical(resolve_activation(args.root, args.release)))
        return
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
        "release-id",
        "operation-id",
        "bootstrap-version",
        "uv-executable",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--trusted-keys", default=None)
    args = vars(parser.parse_args())
    args["trusted_keys"] = _load_trusted_keys(args["trusted_keys"])
    print(canonical(prepare_runtime_candidate(**args)))


if __name__ == "__main__":
    main()
