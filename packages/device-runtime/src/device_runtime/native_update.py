"""Administrator-only package transaction journal; no service stop or activation.

Development protocol. The client package's existing fail-closed hooks remain in
force. This module cannot grant package-hook authorization or activate a release.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from . import native_helper as nh


class UpdateError(ValueError):
    pass


class Debian:
    def metadata(self, path):
        result = subprocess.run(
            ["/usr/bin/dpkg-deb", "--field", str(path), "Package", "Version", "Architecture"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )
        fields = dict(line.split(": ", 1) for line in result.stdout.splitlines() if ": " in line)
        return {key: fields[key] for key in ("Package", "Version", "Architecture")}

    def installed(self, name):
        result = subprocess.run(
            ["/usr/bin/dpkg-query", "-W", "-f=${Status}\n${Version}\n${Architecture}\n", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )
        if result.returncode == 1:
            return {"status": "absent"}
        result.check_returncode()
        lines = result.stdout.splitlines()
        if len(lines) != 3:
            raise UpdateError("invalid package database observation")
        return {"status": lines[0], "version": lines[1], "architecture": lines[2]}

    def apply(self, path, *, lease_fd, receipt_path, intent):
        # dpkg is not killed on a timeout. A separate supervised process must own
        # this call; any lost completion remains unknown, never auto-replayed.
        guard = """import json,os,subprocess,sys,tempfile
from pathlib import Path
receipt=Path(sys.argv[1]); intent=json.loads(sys.argv[2])
r=subprocess.run(['/usr/bin/dpkg','--install',sys.argv[3]],check=False)
fd,tmp=tempfile.mkstemp(prefix='.package-',dir=receipt.parent)
with os.fdopen(fd,'w') as stream:
 json.dump({'state':'sender_reaped','intent':intent,'returncode':r.returncode},stream); stream.flush(); os.fsync(stream.fileno())
os.link(tmp,receipt); os.unlink(tmp)
fd=os.open(receipt.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(fd); os.close(fd)
sys.exit(r.returncode)
"""
        subprocess.run(
            [sys.executable, "-I", "-c", guard, str(receipt_path), json.dumps(intent), str(path)],
            check=True,
            capture_output=True,
            pass_fds=(lease_fd,),
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )


def _atomic(path, value):
    nh._save_new(path, value)


class Updates:
    def __init__(self, registry, *, packages=None, helper=None):
        if sys.platform != "linux" or os.geteuid() != 0:
            raise UpdateError("Linux administrator required")
        self.registry = nh._path(registry)
        nh._trusted(self.registry, directory=True)
        self.helper = helper or nh.Helper(self.registry)
        self.packages = packages or Debian()

    @contextmanager
    def _transaction(self, instance_id, operation_id):
        nh._identifier(instance_id)
        nh._identifier(operation_id)
        # Reuse helper instance lease: starts and update actions cannot overlap.
        with self.helper._lease(instance_id):
            record = self.helper._binding(instance_id)
            directory = self.registry / "updates"
            directory.mkdir(mode=0o700, exist_ok=True)
            nh._trusted(directory, directory=True)
            # Persist the directory entry before an apply intent inside it can
            # become the durable fence observed by the next helper process.
            with nh._directory_fd(self.registry) as fd:
                os.fsync(fd)
            stem = directory / f"{instance_id}--{operation_id}"
            yield record, stem

    def prepare(self, instance_id, operation_id, package_path, *, sha256, version):
        """Register one exact administrator-owned package, without executing it."""
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise UpdateError("exact lowercase SHA256 required")
        package_path = nh._path(package_path)
        nh._trusted(package_path)
        if not package_path.is_file():
            raise UpdateError("package must be a regular administrator-owned file")
        with self._transaction(instance_id, operation_id) as (binding, stem):
            if nh._digest(package_path) != sha256:
                raise UpdateError("package digest differs")
            metadata = self.packages.metadata(package_path)
            if metadata["Architecture"] != "amd64" or metadata["Version"] != version:
                raise UpdateError("package version or architecture differs")
            if metadata["Package"] != "ai-device-native-client":
                raise UpdateError("only the fixed native client package is supported")
            active = nh._digest(Path(binding["root"]) / "active.json")
            intent = {
                "schema_version": 1,
                "instance_id": instance_id,
                "operation_id": operation_id,
                "package_path": str(package_path),
                "sha256": sha256,
                "metadata": metadata,
                "binding_sha256": nh._digest(self.registry / f"{instance_id}.json"),
                "source_active_sha256": active,
                "source_package": self.packages.installed(metadata["Package"]),
            }
            path = stem.with_suffix(".prepared.json")
            if path.exists():
                if json.loads(path.read_text()) != intent:
                    raise UpdateError("operation ID conflicts with prepared transaction")
            else:
                _atomic(path, intent)
            return intent

    def approve(self, instance_id, operation_id, *, prepared_sha256):
        with self._transaction(instance_id, operation_id) as (_, stem):
            path = stem.with_suffix(".prepared.json")
            nh._trusted(path)
            if nh._digest(path) != prepared_sha256:
                raise UpdateError("prepared transaction changed")
            receipt = {"prepared_sha256": prepared_sha256, "approved_by_uid": 0}
            approved = stem.with_suffix(".approved.json")
            if approved.exists():
                if json.loads(approved.read_text()) != receipt:
                    raise UpdateError("approval conflicts")
            else:
                _atomic(approved, receipt)
            return receipt

    def _prepared(self, binding, stem):
        path = stem.with_suffix(".prepared.json")
        approved = stem.with_suffix(".approved.json")
        nh._trusted(path)
        nh._trusted(approved)
        if json.loads(approved.read_text()).get("prepared_sha256") != nh._digest(path):
            raise UpdateError("accurate approval required")
        value = json.loads(path.read_text())
        if nh._digest(self.registry / f"{binding['instance_id']}.json") != value["binding_sha256"]:
            raise UpdateError("instance binding changed")
        package = nh._path(value["package_path"])
        nh._trusted(package)
        if (
            nh._digest(package) != value["sha256"]
            or self.packages.metadata(package) != value["metadata"]
        ):
            raise UpdateError("approved package changed")
        if nh._digest(Path(binding["root"]) / "active.json") != value["source_active_sha256"]:
            raise UpdateError("source active changed")
        return value

    @contextmanager
    def _apply_lease(self, stem, *, existing=False):
        try:
            with nh.package_lease(self.registry, exclusive=True, existing=existing) as fd:
                yield fd
        except nh.HelperError as exc:
            raise UpdateError(str(exc)) from exc

    def apply(self, instance_id, operation_id):
        with self._transaction(instance_id, operation_id) as (binding, stem):
            value = self._prepared(binding, stem)
            started = stem.with_suffix(".apply-intent.json")
            outcome = stem.with_suffix(".outcome.json")
            if started.exists():
                return self._observation(stem, value)
            # One unresolved transaction per instance, including applied pending
            # activation. New IDs never bypass a failed/unknown transaction.
            with (
                self._apply_lease(self.registry / "package-manager") as sender_fd,
                nh._instance_service_lease(Path(binding["root"])),
            ):
                for path in stem.parent.glob("*.apply-intent.json"):
                    raise UpdateError(
                        "existing package transaction requires recovery or activation"
                    )
                # The package payload is shared. Multi-instance drain and
                # registration fencing are not implemented, so reject it.
                registered = set()
                for path in self.registry.glob("*.json"):
                    data = json.loads(path.read_text())
                    # Start ledgers coexist with instance records. An instance
                    # whose ID starts with op-/result- is still a binding.
                    if "root" in data or not path.name.startswith(
                        ("op-", "result-", "sender-", "recovery-")
                    ):
                        registered.add(path.name)
                if registered != {instance_id + ".json"}:
                    raise UpdateError(
                        "shared package update requires exactly one registered instance"
                    )
                for pending in self.registry.glob("op-*.json"):
                    result = pending.with_name(pending.name.replace("op-", "result-", 1))
                    recovery = pending.with_name(pending.name.replace("op-", "recovery-", 1))
                    if (
                        not result.exists()
                        or json.loads(result.read_text()).get("state") == "unknown"
                    ):
                        evidence = json.loads(recovery.read_text()) if recovery.exists() else {}
                        if evidence.get("state") not in {
                            "confirmed_running",
                            "confirmed_stopped",
                        } or evidence.get("intent") != json.loads(pending.read_text()):
                            raise UpdateError("unconfirmed start sender blocks package update")
                observation = self.helper._snapshot(binding)
                if (
                    observation["ActiveState"] not in {"inactive", "failed"}
                    or observation["SubState"] not in {"dead", "failed"}
                    or observation["MainPID"] != "0"
                    or observation["Job"] not in ("", "0")
                ):
                    raise UpdateError(
                        "client must already be stopped and quiescent; no stop is implicit"
                    )
                if self.packages.installed(value["metadata"]["Package"]) != value["source_package"]:
                    raise UpdateError("source package database changed")
                apply_intent = {"prepared_sha256": nh._digest(stem.with_suffix(".prepared.json"))}
                _atomic(started, apply_intent)
                try:
                    self.packages.apply(
                        Path(value["package_path"]),
                        lease_fd=sender_fd,
                        receipt_path=stem.with_suffix(".sender.json"),
                        intent=apply_intent,
                    )
                    observed = self.packages.installed(value["metadata"]["Package"])
                    state = (
                        "package_applied_pending_activation"
                        if self._target(value, observed)
                        else "unknown"
                    )
                except (OSError, subprocess.SubprocessError):
                    observed, state = None, "unknown"
                _atomic(outcome, {"state": state, "package_observation": observed})
            return self._observation(stem, value)

    @staticmethod
    def _target(value, observation):
        return (
            observation.get("status") == "install ok installed"
            and observation.get("version") == value["metadata"]["Version"]
            and observation.get("architecture") == value["metadata"]["Architecture"]
        )

    def _observation(self, stem, value):
        def read(suffix):
            path = stem.with_suffix(suffix)
            return json.loads(path.read_text()) if path.exists() else None

        return {
            "prepared": value,
            "apply_intent": read(".apply-intent.json"),
            "outcome": read(".outcome.json"),
            "recovery": read(".recovery.json"),
            "installed_package": self.packages.installed(value["metadata"]["Package"]),
            "active_sha256": nh._digest(
                Path(self.helper._binding(value["instance_id"])["root"]) / "active.json"
            ),
            "activated": False,
        }

    def reconcile(self, instance_id, operation_id):
        with self._transaction(instance_id, operation_id) as (binding, stem):
            return self._observation(stem, self._prepared(binding, stem))

    def recover(self, instance_id, operation_id):
        """Record observed outcome after sender completion; never reapply."""
        with self._transaction(instance_id, operation_id) as (binding, stem):
            value = self._prepared(binding, stem)
            with self._apply_lease(self.registry / "package-manager", existing=True):
                intent = json.loads(stem.with_suffix(".apply-intent.json").read_text())
                sender = stem.with_suffix(".sender.json")
                nh._trusted(sender)
                completion = json.loads(sender.read_text())
                if completion.get("state") != "sender_reaped" or completion.get("intent") != intent:
                    raise UpdateError("matching package sender completion required")
                installed = self.packages.installed(value["metadata"]["Package"])
                if self._target(value, installed):
                    state = "package_applied_pending_activation"
                elif installed == value["source_package"]:
                    state = "source_package_observed_recovery_required"
                else:
                    raise UpdateError(
                        "partial package state; administrator package repair required"
                    )
                receipt = {
                    "state": state,
                    "package_observation": installed,
                    "sender": completion,
                    "reapplied": False,
                }
                path = stem.with_suffix(".recovery.json")
                if path.exists():
                    if json.loads(path.read_text()) != receipt:
                        raise UpdateError("recovery evidence changed; explicit review required")
                else:
                    _atomic(path, receipt)
            return self._observation(stem, value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--operation-id", required=True)
    modes = parser.add_subparsers(dest="action", required=True)
    prepare = modes.add_parser("prepare")
    prepare.add_argument("--package-path", type=Path, required=True)
    prepare.add_argument("--sha256", required=True)
    prepare.add_argument("--version", required=True)
    approve = modes.add_parser("approve")
    approve.add_argument("--prepared-sha256", required=True)
    modes.add_parser("apply")
    modes.add_parser("reconcile")
    modes.add_parser("recover")
    args = vars(parser.parse_args())
    action = args.pop("action")
    registry = args.pop("registry")
    try:
        print(json.dumps(getattr(Updates(registry), action)(**args), sort_keys=True))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"update refused: {type(exc).__name__}\n")


if __name__ == "__main__":
    main()
