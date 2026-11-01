"""Administrator-only package transaction journal; no service stop or activation.

Development protocol. The client package's existing fail-closed hooks remain in
force. This module cannot grant package-hook authorization or activate a release.
"""

import argparse
import hashlib
import json
import os
import re
import stat
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


def _require_proven_job_execution_fence():
    """Block new job actions until actual worker identity and fencing are proven."""
    raise UpdateError("job worker disabled: execution fence unproven")


def _require_proven_job_recovery_fence():
    """Keep job recovery closed until worker completion is coordinated durably."""
    raise UpdateError("job recovery disabled: worker completion fence unproven")


def _require_proven_package_tree_reaped():
    """Direct-child sender receipts do not prove all package descendants exited."""
    raise UpdateError("package recovery disabled: process tree completion unproven")


def _verify_job_intent(stem, instance_id, operation_id, expected_digest):
    """Revalidate a job under the caller's instance and package leases."""
    path = stem.with_suffix(".job-intent.json")
    nh._trusted(path)
    job = json.loads(path.read_bytes())
    snapshot = stem.with_suffix(".bootstrap")
    nb.validate_snapshot(snapshot)
    expected = {
        "schema_version": 1,
        "state": "job_dispatch_intent",
        "close_protocol": "package-lease-v1",
        "unit": "ai-device-package-" + hashlib.sha256(str(stem).encode()).hexdigest() + ".service",
        "instance_id": instance_id,
        "operation_id": operation_id,
        "prepared_sha256": nh._digest(stem.with_suffix(".prepared.json")),
        "runtime_snapshot_sha256": nh._digest(snapshot / "snapshot.json"),
    }
    if (
        nh._digest(path) != expected_digest
        or not isinstance(job, dict)
        or type(job.get("schema_version")) is not int
        or job != expected
    ):
        raise UpdateError("job intent differs from current transaction")


def _read_runtime_receipt(path, uid):
    # Verify the opened file, not a separate stat of the path, and never read
    # more than the receipt budget even if the owner writes concurrently.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid:
            raise UpdateError("runtime receipt must belong to registered instance owner")
        data = stream.read(256001)
    if len(data) > 256000:
        raise UpdateError("runtime receipt exceeds budget")
    return data


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


def _read_regular(path, error):
    """Read evidence that must be a regular file.

    Several evidence paths live in the instance owner's tree while this module
    runs as root. Opening a FIFO or device there blocks or reads without bound,
    so validate the type before any byte is read.
    """
    info = nh._path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise UpdateError(error)
    return Path(path).read_bytes()


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
            yield self._transaction_locked(instance_id, operation_id)

    @contextmanager
    def _apply_transaction(self, instance_id, operation_id):
        """Do not initialize a transaction for an already-started observation."""
        nh._identifier(instance_id)
        nh._identifier(operation_id)
        with self.helper._lease(instance_id):
            directory = self.registry / "updates"
            stem = directory / f"{len(instance_id)}-{instance_id}--{operation_id}"
            if os.path.lexists(stem.with_suffix(".apply-intent.json")):
                binding, stem = self._existing_transaction_locked(instance_id, operation_id)
                yield binding, stem, True
            else:
                binding, stem = self._transaction_locked(instance_id, operation_id)
                yield binding, stem, False

    @contextmanager
    def _dispatch_transaction(self, instance_id, operation_id, *, require_new_fence):
        """Reject a new dispatch before creating the update transaction directory."""
        nh._identifier(instance_id)
        nh._identifier(operation_id)
        with self.helper._lease(instance_id):
            stem = self.registry / "updates" / f"{len(instance_id)}-{instance_id}--{operation_id}"
            if not os.path.lexists(stem.with_suffix(".job-intent.json")):
                require_new_fence()
            yield self._transaction_locked(instance_id, operation_id)

    @contextmanager
    def _preparation_transaction(self, instance_id, operation_id):
        """Fence preparation writes and metadata queries against supervisor intent."""
        nh._identifier(instance_id)
        nh._identifier(operation_id)
        try:
            with self.helper._lease(instance_id), nh.package_lease(self.registry):
                nh._assert_no_query_supervisor_intent(self.registry)
                yield self._transaction_locked(instance_id, operation_id)
        except nh.HelperError as exc:
            raise UpdateError(str(exc)) from exc

    def _transaction_locked(self, instance_id, operation_id):
        """Initialize canonical update facts while the caller holds the instance lease.

        This private helper neither acquires nor proves ownership of the lease;
        callers must use the canonical helper lease and hold it through use.
        It does not authorize a helper action or external dispatch.
        """
        nh._identifier(instance_id)
        nh._identifier(operation_id)
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
        return record, stem

    def _existing_transaction_locked(self, instance_id, operation_id):
        """Read existing transaction coordinates under the caller's instance lease."""
        record = self.helper._binding(instance_id)
        directory = self.registry / "updates"
        try:
            nh._trusted(directory, directory=True)
        except FileNotFoundError as exc:
            raise UpdateError("update transaction directory missing") from exc
        stem = directory / f"{len(instance_id)}-{instance_id}--{operation_id}"
        return record, stem

    def prepare(
        self,
        instance_id,
        operation_id,
        package_path,
        *,
        sha256,
        version,
        authenticated_candidate=None,
        bootstrap_version=None,
    ):
        """Register one exact administrator-owned package, without executing it."""
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise UpdateError("exact lowercase SHA256 required")
        package_path = nh._path(package_path)
        nh._trusted(package_path)
        if not package_path.is_file():
            raise UpdateError("package must be a regular administrator-owned file")
        with self._preparation_transaction(instance_id, operation_id) as (binding, stem):
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
            if bootstrap_version is not None:
                # The pointer this upgrade will publish names a specific tree, and
                # the activation receipt has to prove that tree is the one in use.
                # Recording it here is what lets the receipt be verified later
                # instead of trusting the caller's report at closure time.
                intent["bootstrap_version"] = bootstrap_version
            path = stem.with_suffix(".prepared.json")
            if path.exists():
                if not nh._same_json(json.loads(path.read_text()), intent):
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
        ):
            raise UpdateError("runtime receipt must belong to registered instance owner")
        receipt_bytes = _read_runtime_receipt(receipt_path, binding["uid"])
        receipt = nc._json(receipt_bytes)
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
        # A venv's bin/python is a symlink to the release's own interpreter, so
        # digesting the link target is the only evidence that exists. Record the
        # resolved path: _check_candidate re-digests evidence paths later, and a
        # symlink would fail that check for every real installation.
        interpreter = python.resolve()
        if not interpreter.is_relative_to(home.resolve()):
            raise UpdateError("runtime interpreter escapes release")
        if not interpreter.is_file():
            raise UpdateError("runtime interpreter unavailable")
        # Read-only evidence, never execute the owner's environment as root.
        return {
            "receipt_path": str(receipt_path),
            "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
            "installation_path": str(home / "installation.json"),
            "installation_sha256": nh._digest(home / "installation.json"),
            "manifest_path": str(home / "bundle/manifest.json"),
            "manifest_sha256": nh._digest(home / "bundle/manifest.json"),
            "python_path": str(interpreter),
            "python_sha256": nh._digest(interpreter),
            "release_sha256": signed_digest,
            "source_active_sha256": request["source_active_sha256"],
            "binding_sha256": request["binding_sha256"],
        }

    def _check_candidate(self, binding, value, *, live=True):
        evidence = value.get("authenticated_candidate")
        if evidence is None:
            return  # Legacy developer journal; not a signed candidate claim.
        if evidence["binding_sha256"] != nh._digest(Path(binding["root"]) / nh.MARKER):
            raise UpdateError("authenticated candidate source changed")
        if live and evidence["source_active_sha256"] != nh._digest(
            Path(binding["root"]) / "active.json"
        ):
            raise UpdateError("authenticated candidate source changed")
        core.inspect_bundle(Path(evidence["manifest_path"]).parent)
        for key in ("receipt", "installation", "manifest", "python"):
            path = Path(evidence[key + "_path"])
            digest = (
                hashlib.sha256(_read_runtime_receipt(path, binding["uid"])).hexdigest()
                if key == "receipt"
                else nh._digest(path)
            )
            if digest != evidence[key + "_sha256"]:
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
        envelope = nc._read_envelope(envelope_path)
        # Recheck binding under the instance lease; prepare() takes it again
        # after verification and compares the complete source evidence.
        with self._preparation_transaction(instance_id, operation_id) as (binding, stem):
            active = nc._json(
                _read_regular(
                    Path(binding["root"]) / "active.json",
                    "instance active pointer must be a regular file",
                )
            )
            payload = nc.verify_release(
                envelope,
                trusted_keys,
                release_id=release_id,
                target=nc.TARGET,
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
                    target=nc.TARGET,
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
            bootstrap_version=bootstrap_version,
        )
        return result

    def approve(self, instance_id, operation_id, *, prepared_sha256):
        with self._preparation_transaction(instance_id, operation_id) as (binding, stem):
            path = stem.with_suffix(".prepared.json")
            nh._trusted(path)
            if nh._digest(path) != prepared_sha256:
                raise UpdateError("prepared transaction changed")
            value = json.loads(path.read_text())
            self._check_candidate(binding, value)
            receipt = {"prepared_sha256": prepared_sha256, "approved_by_uid": 0}
            approved = stem.with_suffix(".approved.json")
            if approved.exists():
                if not nh._valid_admin_approval(json.loads(approved.read_text()), prepared_sha256):
                    raise UpdateError("approval conflicts")
            else:
                _atomic(approved, receipt)
            return receipt

    def _prepared(self, binding, stem):
        return self._prepared_record(binding, stem, live=True)

    def _prepared_record(self, binding, stem, *, live):
        """Validate a transaction's prepared/approval evidence.

        ``live=True`` is the precondition for an operation that changes the
        host: the source active release must still be the one this transaction
        was prepared against, otherwise the approval no longer describes what
        would be replaced.

        ``live=False`` is for recording what a transaction already ended as.
        A successful upgrade moves ``active.json`` by design, so requiring the
        pre-change digest there would make a completed transaction the one thing
        that cannot be closed -- which is exactly the dead end that left
        apply-intent fences behind on every green run. Closure still proves the
        record is the one the administrator approved for this instance and that
        the package bytes are unchanged; it only stops asserting that nothing
        has happened since.
        """
        path = stem.with_suffix(".prepared.json")
        approved = stem.with_suffix(".approved.json")
        nh._trusted(path)
        nh._trusted(approved)
        if not stat.S_ISREG(path.lstat().st_mode) or not stat.S_ISREG(approved.lstat().st_mode):
            raise UpdateError("prepared and approval evidence must be regular files")
        if not nh._valid_admin_approval(json.loads(approved.read_text()), nh._digest(path)):
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
        if live and (
            nh._digest(Path(binding["root"]) / "active.json") != value["source_active_sha256"]
        ):
            raise UpdateError("source active changed")
        self._check_candidate(binding, value, live=live)
        return value

    @contextmanager
    def _apply_lease(self, stem, *, existing=False):
        try:
            with nh.package_lease(self.registry, exclusive=True, existing=existing) as fd:
                nh._assert_no_query_supervisor_intent(self.registry)
                yield fd
        except nh.HelperError as exc:
            raise UpdateError(str(exc)) from exc

    def _prepared_for_mutation(self, binding, stem):
        """Check supervisor intent before metadata subprocesses for mutations."""
        try:
            with nh.package_lease(self.registry):
                nh._assert_no_query_supervisor_intent(self.registry)
                return self._prepared(binding, stem)
        except nh.HelperError as exc:
            raise UpdateError(str(exc)) from exc

    def apply(self, instance_id, operation_id, *, job_intent_sha256=None):
        with self._apply_transaction(instance_id, operation_id) as (binding, stem, observing):
            started = stem.with_suffix(".apply-intent.json")
            outcome = stem.with_suffix(".outcome.json")
            if observing:
                try:
                    nh._trusted(started)
                    if not stat.S_ISREG(started.lstat().st_mode):
                        raise UpdateError("existing apply intent must be a regular file")
                except FileNotFoundError as exc:
                    raise UpdateError("existing apply intent disappeared") from exc
                except nh.HelperError as exc:
                    raise UpdateError("existing apply intent is not trusted") from exc
                value = self._prepared(binding, stem)
                return self._observation(stem, value)
            if os.path.lexists(started):
                raise UpdateError("apply intent appeared during transaction initialization")
            value = self._prepared_for_mutation(binding, stem)
            # One unresolved transaction per instance, including applied pending
            # activation. New IDs never bypass a failed/unknown transaction.
            with (
                self._apply_lease(self.registry / "package-manager") as sender_fd,
                nh._instance_service_lease(Path(binding["root"])),
            ):
                job_path = stem.with_suffix(".job-intent.json")
                # A close marker is checked under the same package lease that
                # protects publication of apply-intent. It never clears fences.
                if os.path.lexists(stem.with_suffix(".job-closed.json")):
                    raise UpdateError("package job closed before execution")
                if job_intent_sha256 is not None:
                    _require_proven_job_execution_fence()
                for pending_job in nh._pending_package_jobs(stem.parent):
                    if pending_job != job_path or job_intent_sha256 is None:
                        raise UpdateError("queued package job blocks direct apply")
                    nh._trusted(pending_job)
                    if nh._digest(pending_job) != job_intent_sha256:
                        raise UpdateError("package job intent changed")
                    _verify_job_intent(stem, instance_id, operation_id, job_intent_sha256)
                if job_intent_sha256 is not None and not job_path.exists():
                    raise UpdateError("package job intent missing")
                for path in stem.parent.glob("*.apply-intent.json"):
                    # A transaction the administrator explicitly retired is
                    # closed, not pending: it must not fence the instance for
                    # ever. Every other intent still blocks, including unknown
                    # and applied-pending-activation.
                    if _retired_file(path):
                        continue
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
                    state = None
                    if result.exists():
                        stored = json.loads(result.read_text())
                        if not isinstance(stored, dict) or stored.get("state") not in (
                            "started", "already_active", "unknown"
                        ):
                            raise UpdateError("invalid start result blocks package update")
                        state = stored["state"]
                    if state == "started":
                        try:
                            nh._require_proven_start_sender_tree()
                        except nh.HelperError as exc:
                            raise UpdateError(str(exc)) from exc
                    if state in (None, "unknown"):
                        try:
                            start_intent = json.loads(pending.read_text())
                            nh._require_current_start_barrier(start_intent)
                            nh._require_proven_start_sender_tree()
                            nh._read_start_recovery(recovery, start_intent)
                        except (OSError, ValueError, TypeError, nh.HelperError) as exc:
                            raise UpdateError(
                                "unconfirmed start sender blocks package update"
                            ) from exc
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
                # Recorded before dpkg runs: afterwards the directory this names
                # is gone, so the "before" value cannot be recovered.
                bootstrap_before = self._bootstrap_name()
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
                    guard_state = None
                    if outcome.exists():
                        nh._trusted(outcome)
                        published = json.loads(outcome.read_bytes())
                        if published.get("intent") != apply_intent:
                            raise UpdateError("package guard outcome intent differs")
                        guard_state = published.get("state")
                else:
                    _atomic(outcome, {"state": state, "package_observation": observed})
                    guard_state = state
                # ``applied_package`` is a claim that the package manager finished
                # replacing the tree. The database alone cannot support it: a
                # refused install of an already-current version leaves the same
                # version installed, so ``installed == target`` holds before,
                # during and after a failed dpkg run. Requiring the guard's own
                # success verdict is what separates "applied" from "already was".
                installed = self.packages.installed(value["metadata"]["Package"])
                if self._applied(value, installed, guard_state):
                    applied = {
                        "state": "applied_package",
                        "package_observation": installed,
                        "source_package": value["source_package"],
                        # The pointer still names the tree the package replaced.
                        # Capturing it here is what lets reconcile explain the
                        # window instead of guessing.
                        "bootstrap_before": bootstrap_before,
                        "target_bootstrap": value.get("bootstrap_version"),
                    }
                    path = stem.with_suffix(".applied.json")
                    if os.path.lexists(path):
                        nh._trusted(path)
                        if not nh._same_json(json.loads(path.read_text()), applied):
                            raise UpdateError(
                                "applied evidence changed; explicit review required"
                            )
                    else:
                        _atomic(path, applied)
            return self._observation(stem, value)

    @staticmethod
    def _bootstrap_name():
        try:
            return nb.current_name()
        except (nb.BootstrapError, OSError, nh.HelperError):
            return None

    @staticmethod
    def _target(value, observation):
        return (
            observation.get("status") == "install ok installed"
            and observation.get("version") == value["metadata"]["Version"]
            and observation.get("architecture") == value["metadata"]["Architecture"]
        )

    def _applied(self, value, observation, guard_state):
        """Applied means the package manager reported success for this intent.

        On Debian the independent guard publishes both ``sender_reaped`` (the
        process exited) and ``package_applied_pending_activation`` (exit status
        zero *and* the database shows the exact target). Only the latter is
        evidence that a replacement happened; a zero exit alone would accept a
        no-op, and a matching version alone would accept a refusal.
        """
        if guard_state != "package_applied_pending_activation":
            return False
        return self._target(value, observation)

    def switch_helper(self, instance_id, operation_id, *, expected_version):
        """Move ``bootstrap/current`` to the installed version and record it.

        Applying the package already replaced the versioned bootstrap directory,
        so between ``apply`` and this call the pointer may name a tree that no
        longer exists. This is the step that closes that window, which is why it
        is a transaction state of its own rather than a side effect of apply.

        It takes the package-manager lease as well as the instance lease: the
        pointer is host-global, so two instances switching at once must not
        interleave, and neither may run while another package action is in
        flight. Success is published as ``helper_switched``; a failure leaves
        ``applied_package`` intact and is never retried automatically.
        """
        # `observing` only says an apply intent already exists; it is the normal
        # case here, since a package must have been applied before switching.
        with self._apply_transaction(instance_id, operation_id) as (binding, stem, _observing):
            with self._apply_lease(self.registry / "package-manager"):
                # Already holding the exclusive package lease, so read the record
                # directly: _prepared_for_mutation would take a shared lease on
                # the same file and deadlock against ourselves.
                value = self._prepared(binding, stem)
                applied = stem.with_suffix(".applied.json")
                if not os.path.lexists(applied):
                    raise UpdateError("package is not applied; nothing to switch")
                nh._trusted(applied)
                record = json.loads(applied.read_text())
                if not isinstance(record, dict) or record.get("state") != "applied_package":
                    raise UpdateError("package apply is not in a switchable state")
                installed = self.packages.installed(value["metadata"]["Package"])
                if not self._target(value, installed):
                    raise UpdateError("installed package no longer matches the applied record")
                current = nb.trusted_helper_bootstrap()
                previous = current.parent.name
                if expected_version == previous:
                    # The pointer already names the target. Either this call is a
                    # replay of a switch that did happen (the receipt is on disk)
                    # or the package post-install step moved the pointer itself,
                    # so there was never a separate "before" here. Reusing the
                    # existing receipt keeps a genuine switch as the record;
                    # only the second case gets an observation receipt.
                    path = stem.with_suffix(".helper.json")
                    if os.path.lexists(path):
                        nh._trusted(path)
                        stored = json.loads(path.read_text())
                        return {**self._observation(stem, value), "helper_switched": stored}
                    receipt = {
                        "state": "helper_switched",
                        "previous": previous,
                        "release": previous,
                        "probe": "ok",
                        "observed_not_moved": True,
                    }
                    _atomic(path, receipt)
                    return {**self._observation(stem, value), "helper_switched": receipt}
                published = nb.publish_pointer(expected_version)
                # Prove the new entrypoint actually runs before recording success.
                # A pointer that names an unrunnable tree is worse than the old
                # one: it would turn every later start into a hard failure.
                probe = subprocess.run(
                    [
                        str(nb.BOOTSTRAP_ROOT / published / "runtime/bin/python3.13"),
                        "-I",
                        "-B",
                        "-c",
                        "from device_runtime import native_helper",
                    ],
                    check=False,
                    capture_output=True,
                    timeout=60,
                    env={"PATH": "/usr/bin:/bin", "LANG": "C"},
                )
                if probe.returncode != 0:
                    # Put the old pointer back: it still names a directory the
                    # applied package replaced, but restoring beats leaving a
                    # known-broken entrypoint recorded as success.
                    nb.publish_pointer(previous)
                    raise UpdateError("new helper entrypoint failed its probe")
                receipt = {
                    "state": "helper_switched",
                    "previous": previous,
                    "release": published,
                    "probe": "ok",
                }
                path = stem.with_suffix(".helper.json")
                if os.path.lexists(path):
                    nh._trusted(path)
                    if not nh._same_json(json.loads(path.read_text()), receipt):
                        raise UpdateError("helper switch evidence changed; explicit review required")
                else:
                    _atomic(path, receipt)
            return {**self._observation(stem, value), "helper_switched": receipt}

    def record_activation(self, instance_id, operation_id):
        """Record that this transaction's release is the active one.

        A successful activate is a terminal state, but nothing wrote it down:
        ``_next_step`` only recognised ``.retired.json``, so every completed
        upgrade kept reporting ``activate`` and its ``apply-intent`` kept fencing
        the instance. The operator had to retire a transaction that had actually
        succeeded, which records the wrong fact.

        This receipt is written only from observation. The owner reports success
        from its own process; this reads ``active.json`` under the runtime root
        and refuses unless that file really names the release this transaction
        prepared, with a matching activation id and the expected bootstrap tree.
        A crash between activation and this write therefore leaves an intent that
        still fences, and re-running this call only observes the same state again.

        Nothing is deleted: the intent, sender, package log and outcome stay as
        audit evidence. The fence is released by the receipt, not by absence.
        """
        with self._transaction(instance_id, operation_id) as (binding, stem):
            value = self._prepared_record(binding, stem, live=False)
            intent_path = stem.with_suffix(".apply-intent.json")
            activated = stem.with_suffix(".activated.json")
            if not os.path.lexists(intent_path):
                raise UpdateError("no apply intent to close by activation")
            nh._trusted(intent_path)
            if os.path.lexists(activated):
                nh._trusted(activated)
                stored = json.loads(activated.read_text())
                if not isinstance(stored, dict):
                    raise UpdateError("activation evidence must be an object")
                # Re-recording is an observation, not a new transaction. Refuse a
                # receipt that names a different intent rather than overwriting
                # evidence another closure depends on.
                if stored.get("intent_sha256") != nh._digest(intent_path):
                    raise UpdateError("activation evidence belongs to another intent")
                return {**self._observation(stem, value), "activation": stored}
            target = self._activation_target(value, binding)
            observed = self._active_record(binding)
            if observed.get("release") != target["release"]:
                raise UpdateError("active release does not match the activated candidate")
            if observed.get("release_version") != target["release_version"]:
                raise UpdateError("active release version does not match the candidate")
            activation_id = observed.get("activation_id")
            if not isinstance(activation_id, str) or not activation_id:
                raise UpdateError("active release carries no activation id")
            if self._bootstrap_name() != target["bootstrap_version"]:
                raise UpdateError("bootstrap tree does not match the activated package")
            receipt = {
                "state": "activated",
                "intent_sha256": nh._digest(intent_path),
                "prepared_sha256": nh._digest(stem.with_suffix(".prepared.json")),
                "release": observed["release"],
                "release_version": observed["release_version"],
                "activation_id": activation_id,
                "bootstrap_now": self._bootstrap_name(),
                "replay": "none",
            }
            _atomic(activated, receipt)
            return {**self._observation(stem, value), "activation": receipt}

    @staticmethod
    def _activation_target(value, binding):
        """Resolve the release this transaction must have activated.

        The candidate evidence points at the owner's runtime receipt, which names
        the release directory that was prepared. That receipt is re-read here
        through ``_read_runtime_receipt`` so the digest and ownership checks are
        repeated, and the release name must still be a plain directory entry.

        A transaction without signed candidate evidence predates signed
        installation and cannot prove what it activated, so it stays fenced.
        """
        evidence = value.get("authenticated_candidate")
        if not isinstance(evidence, dict):
            raise UpdateError("transaction has no authenticated candidate evidence")
        receipt = json.loads(
            _read_runtime_receipt(Path(evidence["receipt_path"]), binding["uid"])
        )
        candidate = receipt.get("candidate")
        if not isinstance(candidate, dict):
            raise UpdateError("runtime receipt names no candidate")
        release = candidate.get("release")
        if not isinstance(release, str) or Path(release).name != release or release in {".", ".."}:
            raise UpdateError("runtime receipt names an invalid release")
        release_version = candidate.get("release_version")
        if not isinstance(release_version, str) or not release_version:
            raise UpdateError("runtime receipt names no release version")
        bootstrap_version = value.get("bootstrap_version")
        if not isinstance(bootstrap_version, str) or not bootstrap_version:
            raise UpdateError("transaction records no target bootstrap tree")
        return {
            "release": release,
            "release_version": release_version,
            "bootstrap_version": bootstrap_version,
        }

    @staticmethod
    def _active_record(binding):
        """Read the instance's active pointer as a regular owner-owned file."""
        root = Path(binding["root"])
        return json.loads(
            _read_regular(
                root / "active.json",
                "instance active pointer must be a regular file",
            )
        )

    def retire(self, instance_id, operation_id):
        """Administrator closure for an apply transaction that will never finish.

        ``apply-intent`` is a durable start gate: it blocks new starts, new
        registrations and every later package transaction, and nothing removes it.
        Without an explicit closure a transaction that ended ``unknown`` (or was
        applied but never switched) fences the instance forever.

        This records that the administrator accepted the observed end state. It
        never replays the package, never clears any evidence, and never claims
        success: the intent, sender, package log and outcome all stay in place,
        and the receipt only names what was observed when the closure happened.

        Holding the exclusive package lease is the proof that no sender survives:
        the guard inherits that fd and dpkg's whole process tree keeps it, so the
        lease can only be taken after every descendant has exited. That is the
        evidence ``recover`` lacks and the reason this path is separate from it.
        """
        with self._transaction(instance_id, operation_id) as (binding, stem):
            # Closing is not a change: the whole point is that the transaction
            # already ended, possibly by moving active.json. Verifying the
            # pre-change source digest here would refuse to close exactly the
            # transactions that succeeded.
            value = self._prepared_record(binding, stem, live=False)
            intent_path = stem.with_suffix(".apply-intent.json")
            retired = stem.with_suffix(".retired.json")
            with self._apply_lease(self.registry / "package-manager", existing=True):
                if not os.path.lexists(intent_path):
                    raise UpdateError("no apply intent to retire")
                nh._trusted(intent_path)
                intent = json.loads(intent_path.read_text())
                if not isinstance(intent, dict):
                    raise UpdateError("apply intent must be an object")
                # A queued job owns its own closure protocol; retiring underneath
                # it would clear a fence the job worker still depends on.
                if nh._pending_package_jobs(stem.parent):
                    raise UpdateError("queued package job requires its own closure")
                installed = self.packages.installed(value["metadata"]["Package"])
                receipt = {
                    "state": "retired_without_replay",
                    "intent_sha256": nh._digest(intent_path),
                    "package_observation": installed,
                    "package_matches_target": self._target(value, installed),
                    "bootstrap_now": self._bootstrap_name(),
                    "target_bootstrap": value.get("bootstrap_version"),
                    "retry_allowed": False,
                    "fence_retained": True,
                    "replay": "none",
                }
                if os.path.lexists(retired):
                    nh._trusted(retired)
                    if not nh._same_json(json.loads(retired.read_text()), receipt):
                        raise UpdateError("retirement evidence changed; explicit review required")
                    return self._observation(stem, value)
                _atomic(retired, receipt)
            return self._observation(stem, value)

    def _observation(self, stem, value):
        def read(suffix):
            path = stem.with_suffix(suffix)
            if not os.path.lexists(path):
                return None
            try:
                nh._trusted(path)
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise UpdateError("update observation evidence must be a regular file")
                return json.loads(path.read_text())
            except nh.HelperError as exc:
                raise UpdateError("update observation evidence is not trusted") from exc

        applied = read(".applied.json")
        switched = read(".helper.json")
        retired = read(".retired.json")
        activated = read(".activated.json")
        return {
            "prepared": value,
            "apply_intent": read(".apply-intent.json"),
            "outcome": read(".outcome.json"),
            "recovery": read(".recovery.json"),
            "retired": retired,
            "activation": activated,
            "applied": applied,
            "helper_switched": switched,
            "bootstrap_now": self._bootstrap_name(),
            "next_step": _next_step(applied, switched, retired, activated),
            "installed_package": self.packages.installed(value["metadata"]["Package"]),
            "active_sha256": nh._digest(
                Path(self.helper._binding(value["instance_id"])["root"]) / "active.json"
            ),
            "activated": activated is not None,
        }

    def reconcile(self, instance_id, operation_id):
        nh._identifier(instance_id)
        nh._identifier(operation_id)
        with self.helper._lease(instance_id):
            binding, stem = self._existing_transaction_locked(instance_id, operation_id)
            return self._observation(stem, self._prepared(binding, stem))

    def recover(self, instance_id, operation_id, *, job_intent_sha256=None):
        """Record observed outcome after sender completion; never reapply."""
        if job_intent_sha256 is not None:
            _require_proven_job_recovery_fence()
        with self._transaction(instance_id, operation_id) as (binding, stem):
            value = self._prepared_for_mutation(binding, stem)
            with self._apply_lease(self.registry / "package-manager", existing=True):
                if job_intent_sha256 is None and (
                    os.path.lexists(stem.with_suffix(".job-intent.json"))
                    or os.path.lexists(stem.with_suffix(".job-recovery.json"))
                ):
                    raise UpdateError("job recovery requires explicit job binding")
                if job_intent_sha256 is not None:
                    try:
                        _verify_job_intent(stem, instance_id, operation_id, job_intent_sha256)
                    except UpdateError as exc:
                        raise UpdateError("job recovery intent differs") from exc
                    job_receipt = stem.with_suffix(".job-recovery.json")
                    recovery = stem.with_suffix(".recovery.json")
                    if job_receipt.exists():
                        nh._trusted(job_receipt)
                        if not recovery.exists():
                            raise UpdateError("job recovery evidence without underlying recovery")
                        nh._trusted(recovery)
                        existing_recovery = json.loads(recovery.read_bytes())
                        if not isinstance(existing_recovery, dict):
                            raise UpdateError("job recovery evidence changed")
                        expected_job_receipt = {
                            "state": "job_reconciled_without_replay",
                            "job_intent_sha256": job_intent_sha256,
                            "recovery_sha256": nh._digest(recovery),
                            "recovery_state": existing_recovery.get("state"),
                            "retry_allowed": False,
                            "fence_retained": True,
                        }
                        if not nh._same_json(json.loads(job_receipt.read_bytes()), expected_job_receipt):
                            raise UpdateError("job recovery evidence changed")
                intent_path = stem.with_suffix(".apply-intent.json")
                nh._trusted(intent_path)
                intent = json.loads(intent_path.read_text())
                if isinstance(self.packages, Debian) or (
                    isinstance(intent, dict) and "runtime_snapshot_sha256" in intent
                ):
                    _require_proven_package_tree_reaped()
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
                if not nh._valid_sender_completion(completion, intent):
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
                    if not nh._same_json(json.loads(path.read_text()), receipt):
                        raise UpdateError("recovery evidence changed; explicit review required")
                else:
                    _atomic(path, receipt)
            return self._observation(stem, value)


def _retired_file(intent_path):
    """Report whether this exact intent carries a terminal closure.

    Two receipts close a transaction, and they are not interchangeable:
    ``.retired.json`` records an administrator accepting a dead end without a
    replay, while ``.activated.json`` records that the prepared release actually
    became the active one. Both bind this intent's digest, so neither can be
    shifted onto a different transaction by renaming or rewriting the intent.

    Corrupt, untrusted or mismatched evidence reports False: the safe answer here
    is "still fenced", never "closed".
    """
    name = intent_path.name
    suffix = ".apply-intent.json"
    if not name.endswith(suffix):
        return False
    digest = nh._digest(intent_path)
    for receipt in (
        intent_path.with_name(name[: -len(suffix)] + ".retired.json"),
        intent_path.with_name(name[: -len(suffix)] + ".activated.json"),
    ):
        if _terminal_receipt(receipt, digest):
            return True
    return False


def _terminal_receipt(receipt, intent_sha256):
    """Check one closure receipt against the intent it claims to close."""
    if not os.path.lexists(receipt):
        return False
    try:
        nh._trusted(receipt)
        stored = json.loads(receipt.read_text())
        if not isinstance(stored, dict):
            return False
        state = stored.get("state")
        if state == "retired_without_replay":
            if stored.get("retry_allowed") is not False:
                return False
        elif state != "activated":
            return False
        return stored.get("intent_sha256") == intent_sha256
    except (OSError, ValueError, nh.HelperError):
        return False


def _next_step(applied, switched, retired=None, activated=None):
    """Name what the transaction still needs, without performing it.

    Reconcile is read-only on purpose: the next move -- switching the helper,
    activating a release, or accepting a partial state -- changes what runs on
    the host, so it stays an explicit call.
    """
    if activated is not None and activated.get("state") == "activated":
        # A completed transaction: the prepared release is the active one, so
        # nothing remains and the instance is free for a new one.
        return "activated"
    if retired is not None and retired.get("state") == "retired_without_replay":
        # An accepted dead end: nothing remains for this transaction, so the
        # instance is free for a new one. The evidence stays on disk.
        return "retired"
    if applied is None:
        return "apply"
    if applied.get("state") != "applied_package":
        return "manual_review"
    if switched is None:
        return "switch_helper"
    if switched.get("state") != "helper_switched":
        return "manual_review"
    return "activate"


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
    switch = modes.add_parser("switch-helper")
    switch.add_argument("--expected-version", required=True)
    modes.add_parser("reconcile")
    modes.add_parser("recover")
    # Explicit administrator closure. Takes no input beyond the transaction
    # coordinates: it accepts what is on disk and refuses to replay anything.
    modes.add_parser("retire")
    args = vars(parser.parse_args())
    action = args.pop("action").replace("-", "_")
    if action == "prepare_signed":
        keys_path = nh._path(args["trusted_keys"])
        nh._trusted(keys_path)
        args["trusted_keys"] = nc._json(
            _read_regular(keys_path, "trusted keys must be a regular file")
        )
    registry = args.pop("registry")
    try:
        print(json.dumps(getattr(Updates(registry), action)(**args), sort_keys=True))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"update refused: {type(exc).__name__}\n")


if __name__ == "__main__":
    main()
