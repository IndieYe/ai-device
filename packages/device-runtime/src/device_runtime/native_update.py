"""Administrator-only package transaction journal; no service stop or activation.

Development protocol. The client package's existing fail-closed hooks remain in
force. This module cannot grant package-hook authorization or activate a release.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import installation as core
from . import native_bootstrap as nb
from . import native_candidate as nc
from . import native_helper as nh


class UpdateError(ValueError):
    pass


class Debian:
    def prepare_execution(self, stem):
        directory = stem.with_suffix(".bootstrap")
        if Path(sys.base_prefix) == directory / "runtime":
            # A systemd worker is already running from this exact snapshot.
            nb.validate_snapshot(directory)
        else:
            nb.snapshot_runtime(Path(sys.base_prefix), directory)
        return {"runtime_snapshot_sha256": nh._digest(directory / "snapshot.json")}

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
        guard = r"""import json,os,subprocess,sys,tempfile
from pathlib import Path
receipt=Path(sys.argv[1]); intent=json.loads(sys.argv[2])
def publish(path,value):
 fd,tmp=tempfile.mkstemp(prefix='.package-',dir=path.parent)
 with os.fdopen(fd,'w') as stream:
  json.dump(value,stream); stream.flush(); os.fsync(stream.fileno())
 os.link(tmp,path); os.unlink(tmp)
 fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(fd); os.close(fd)
with receipt.with_suffix('').with_suffix('.package.log').open('xb') as log:
 r=subprocess.run(['/usr/bin/dpkg','--install',sys.argv[3]],check=False,stdout=log,stderr=subprocess.STDOUT)
 log.flush(); os.fsync(log.fileno())
publish(receipt,{'state':'sender_reaped','intent':intent,'returncode':r.returncode})
if 'target_package' in intent:
 target=intent['target_package']; observed=None
 try:
  query=subprocess.run(['/usr/bin/dpkg-query','-W','-f=${Status}\\n${Version}\\n${Architecture}\\n',target['name']],check=False,capture_output=True,text=True,timeout=30)
  lines=query.stdout.splitlines()
  if query.returncode==0 and len(lines)==3:
   observed=dict(zip(('status','version','architecture'),lines))
 except (OSError,subprocess.SubprocessError): pass
 expected={'status':'install ok installed','version':target['version'],'architecture':target['architecture']}
 state='package_applied_pending_activation' if r.returncode==0 and observed==expected else 'unknown'
 publish(receipt.with_suffix('').with_suffix('.outcome.json'),{'state':state,'package_observation':observed,'intent':intent})
sys.exit(r.returncode)
"""
        directory = receipt_path.with_suffix("").with_suffix(".bootstrap")
        nb.validate_snapshot(directory)
        if nh._digest(directory / "snapshot.json") != intent.get("runtime_snapshot_sha256"):
            raise UpdateError("package sender runtime snapshot changed")
        python = directory / "runtime/bin/python3.13"
        subprocess.run(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                guard,
                str(receipt_path),
                json.dumps(intent),
                str(path),
            ],
            check=True,
            capture_output=True,
            pass_fds=(lease_fd,),
            # dpkg requires its standard administrative helpers on PATH.
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
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
            # IDs permit '--'; length-prefix the first component so distinct
            # (instance, operation) pairs cannot alias the same transaction.
            stem = directory / f"{len(instance_id)}-{instance_id}--{operation_id}"
            yield record, stem

    def prepare(
        self,
        instance_id,
        operation_id,
        package_path,
        *,
        sha256,
        version,
        authenticated_candidate=None,
    ):
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
            if authenticated_candidate is not None:
                intent["authenticated_candidate"] = authenticated_candidate
                self._check_candidate(binding, intent)
            path = stem.with_suffix(".prepared.json")
            if path.exists():
                if json.loads(path.read_text()) != intent:
                    raise UpdateError("operation ID conflicts with prepared transaction")
            else:
                _atomic(path, intent)
            return intent

    def _runtime_evidence(self, binding, receipt_path, payload):
        root = Path(binding["root"])
        receipt_path = Path(receipt_path)
        if (
            receipt_path.is_symlink()
            or receipt_path.parent != root / "native-candidates"
            or receipt_path.resolve() != receipt_path
            or receipt_path.stat().st_uid != binding["uid"]
        ):
            raise UpdateError("runtime receipt must belong to registered instance owner")
        if receipt_path.stat().st_size > 256000:
            raise UpdateError("runtime receipt exceeds budget")
        receipt = nc._json(receipt_path.read_bytes())
        request = receipt.get("request", {})
        signed_digest = hashlib.sha256(nc.canonical(payload).encode()).hexdigest()
        if (
            receipt.get("state") != "runtime_prepared"
            or receipt.get("activated") is not False
            or request.get("release_sha256") != signed_digest
        ):
            raise UpdateError("runtime receipt differs from authenticated release")
        if request.get("source_active_sha256") != nh._digest(root / "active.json") or request.get(
            "binding_sha256"
        ) != nh._digest(root / nh.MARKER):
            raise UpdateError("runtime source or binding changed")
        release = receipt["candidate"]["release"]
        if not isinstance(release, str) or Path(release).name != release or release in {".", ".."}:
            raise UpdateError("invalid runtime candidate release")
        home = root / "releases" / release
        if home.is_symlink() or not home.resolve().is_relative_to((root / "releases").resolve()):
            raise UpdateError("runtime release escapes instance")
        manifest = core.inspect_bundle(home / "bundle")
        if (
            nh._digest(home / "bundle/manifest.json") != payload["package"]["manifest_sha256"]
            or core._fingerprint(manifest) != receipt["candidate"]["bundle_sha256"]
        ):
            raise UpdateError("runtime bundle differs from signed payload")
        if nc._json((home / "installation.json").read_bytes()) != receipt["candidate"]:
            raise UpdateError("runtime installation evidence differs")
        python = home / "client/venv/bin/python"
        if not python.is_file() or not os.access(python, os.X_OK):
            raise UpdateError("runtime interpreter unavailable")
        # Read-only evidence, never execute the owner's environment as root.
        return {
            "receipt_path": str(receipt_path),
            "receipt_sha256": nh._digest(receipt_path),
            "installation_path": str(home / "installation.json"),
            "installation_sha256": nh._digest(home / "installation.json"),
            "manifest_path": str(home / "bundle/manifest.json"),
            "manifest_sha256": nh._digest(home / "bundle/manifest.json"),
            "python_path": str(python),
            "python_sha256": nh._digest(python),
            "release_sha256": signed_digest,
            "source_active_sha256": request["source_active_sha256"],
            "binding_sha256": request["binding_sha256"],
        }

    def _check_candidate(self, binding, value):
        evidence = value.get("authenticated_candidate")
        if evidence is None:
            return  # Legacy developer journal; not a signed candidate claim.
        if evidence["source_active_sha256"] != nh._digest(
            Path(binding["root"]) / "active.json"
        ) or evidence["binding_sha256"] != nh._digest(Path(binding["root"]) / nh.MARKER):
            raise UpdateError("authenticated candidate source changed")
        core.inspect_bundle(Path(evidence["manifest_path"]).parent)
        for key in ("receipt", "installation", "manifest", "python"):
            path = Path(evidence[key + "_path"])
            if nh._digest(path) != evidence[key + "_sha256"]:
                raise UpdateError("authenticated runtime evidence changed")

    def prepare_signed(
        self,
        instance_id,
        operation_id,
        package_path,
        *,
        envelope_path,
        trusted_keys,
        release_id,
        runtime_receipt,
        bootstrap_version,
    ):
        package_path = nh._path(package_path)
        envelope_path = nh._path(envelope_path)
        nh._trusted(package_path)
        nh._trusted(envelope_path)
        if envelope_path.stat().st_size > 256000:
            raise UpdateError("release envelope exceeds budget")
        envelope = nc._json(envelope_path.read_bytes())
        # Recheck binding under the instance lease; prepare() takes it again
        # after verification and compares the complete source evidence.
        with self._transaction(instance_id, operation_id) as (binding, stem):
            active = nc._json((Path(binding["root"]) / "active.json").read_bytes())
            payload = nc.verify_release(
                envelope,
                trusted_keys,
                release_id=release_id,
                target="linux-amd64-deb",
                source_version=active["release_version"],
                bootstrap_version=bootstrap_version,
            )
            with tempfile.TemporaryDirectory(prefix=".authenticate-", dir=stem.parent) as temporary:
                authentication = nc.prepare_candidate(
                    package_path,
                    envelope_path,
                    Path(temporary) / "candidate",
                    trusted_keys=trusted_keys,
                    release_id=release_id,
                    target="linux-amd64-deb",
                    source_version=active["release_version"],
                    bootstrap_version=bootstrap_version,
                )
                evidence = self._runtime_evidence(binding, runtime_receipt, payload)
                if evidence["release_sha256"] != authentication["release_sha256"]:
                    raise UpdateError("release changed during authentication")
            self._check_candidate(binding, {"authenticated_candidate": evidence})
        result = self.prepare(
            instance_id,
            operation_id,
            package_path,
            sha256=payload["package"]["sha256"],
            version=payload["package"]["version"],
            authenticated_candidate=evidence,
        )
        return result

    def approve(self, instance_id, operation_id, *, prepared_sha256):
        with self._transaction(instance_id, operation_id) as (binding, stem):
            path = stem.with_suffix(".prepared.json")
            nh._trusted(path)
            if nh._digest(path) != prepared_sha256:
                raise UpdateError("prepared transaction changed")
            value = json.loads(path.read_text())
            self._check_candidate(binding, value)
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
        self._check_candidate(binding, value)
        return value

    @contextmanager
    def _apply_lease(self, stem, *, existing=False):
        try:
            with nh.package_lease(self.registry, exclusive=True, existing=existing) as fd:
                yield fd
        except nh.HelperError as exc:
            raise UpdateError(str(exc)) from exc

    def apply(self, instance_id, operation_id, *, job_intent_sha256=None):
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
                job_path = stem.with_suffix(".job-intent.json")
                for pending_job in stem.parent.glob("*.job-intent.json"):
                    if pending_job != job_path or job_intent_sha256 is None:
                        raise UpdateError("queued package job blocks direct apply")
                    nh._trusted(pending_job)
                    if nh._digest(pending_job) != job_intent_sha256:
                        raise UpdateError("package job intent changed")
                    job_value = json.loads(pending_job.read_bytes())
                    if job_value.get("prepared_sha256") != nh._digest(
                        stem.with_suffix(".prepared.json")
                    ):
                        raise UpdateError("queued job prepared evidence changed")
                    nb.validate_snapshot(stem.with_suffix(".bootstrap"))
                    if job_value.get("runtime_snapshot_sha256") != nh._digest(
                        stem.with_suffix(".bootstrap") / "snapshot.json"
                    ):
                        raise UpdateError("queued job runtime changed")
                if job_intent_sha256 is not None and not job_path.exists():
                    raise UpdateError("package job intent missing")
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
                if isinstance(self.packages, Debian):
                    # Complete and verify the independent runtime before the
                    # irreversible intent; incomplete snapshots never recopy.
                    apply_intent.update(self.packages.prepare_execution(stem))
                    apply_intent["target_package"] = {
                        "name": value["metadata"]["Package"],
                        "version": value["metadata"]["Version"],
                        "architecture": value["metadata"]["Architecture"],
                    }
                _atomic(started, apply_intent)
                try:
                    self.packages.apply(
                        Path(value["package_path"]),
                        lease_fd=sender_fd,
                        receipt_path=stem.with_suffix(".sender.json"),
                        intent=apply_intent,
                    )
                    if not isinstance(self.packages, Debian):
                        observed = self.packages.installed(value["metadata"]["Package"])
                        state = (
                            "package_applied_pending_activation"
                            if self._target(value, observed)
                            else "unknown"
                        )
                except (OSError, subprocess.SubprocessError):
                    observed, state = None, "unknown"
                if isinstance(self.packages, Debian):
                    # The independent guard owns the outcome. Its absence is
                    # unknown, never authority for the parent to guess success.
                    if outcome.exists():
                        nh._trusted(outcome)
                        if json.loads(outcome.read_bytes()).get("intent") != apply_intent:
                            raise UpdateError("package guard outcome intent differs")
                else:
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

    def recover(self, instance_id, operation_id, *, job_intent_sha256=None):
        """Record observed outcome after sender completion; never reapply."""
        with self._transaction(instance_id, operation_id) as (binding, stem):
            value = self._prepared(binding, stem)
            with self._apply_lease(self.registry / "package-manager", existing=True):
                if job_intent_sha256 is not None:
                    job_path = stem.with_suffix(".job-intent.json")
                    nh._trusted(job_path)
                    job = json.loads(job_path.read_bytes())
                    if (
                        nh._digest(job_path) != job_intent_sha256
                        or job.get("instance_id") != instance_id
                        or job.get("operation_id") != operation_id
                        or job.get("prepared_sha256")
                        != nh._digest(stem.with_suffix(".prepared.json"))
                    ):
                        raise UpdateError("job recovery intent differs")
                intent_path = stem.with_suffix(".apply-intent.json")
                nh._trusted(intent_path)
                intent = json.loads(intent_path.read_text())
                expected = {"prepared_sha256": nh._digest(stem.with_suffix(".prepared.json"))}
                if "runtime_snapshot_sha256" in intent:
                    directory = stem.with_suffix(".bootstrap")
                    nb.validate_snapshot(directory)
                    expected.update(
                        {
                            "runtime_snapshot_sha256": nh._digest(directory / "snapshot.json"),
                            "target_package": {
                                "name": value["metadata"]["Package"],
                                "version": value["metadata"]["Version"],
                                "architecture": value["metadata"]["Architecture"],
                            },
                        }
                    )
                if intent != expected:
                    raise UpdateError("apply intent differs from approved preparation")
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
    signed = modes.add_parser("prepare-signed")
    for name in (
        "package-path",
        "envelope-path",
        "trusted-keys",
        "release-id",
        "runtime-receipt",
        "bootstrap-version",
    ):
        signed.add_argument("--" + name, required=True)
    modes.add_parser("apply")
    modes.add_parser("reconcile")
    modes.add_parser("recover")
    args = vars(parser.parse_args())
    action = args.pop("action").replace("-", "_")
    if action == "prepare_signed":
        keys_path = nh._path(args["trusted_keys"])
        nh._trusted(keys_path)
        args["trusted_keys"] = nc._json(keys_path.read_bytes())
    registry = args.pop("registry")
    try:
        print(json.dumps(getattr(Updates(registry), action)(**args), sort_keys=True))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"update refused: {type(exc).__name__}\n")


if __name__ == "__main__":
    main()
