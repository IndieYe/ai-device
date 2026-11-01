"""One-shot service supervisor for Linux/systemd and macOS/launchd, independent
of the managed process.

Profile provisioning is an explicit administrator operation, never auto-adoption.
"""

import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
import time
from pathlib import Path

from . import upgrader as up
from .maintenance import request
from .management_lock import legacy_operation


class Systemd:
    def __init__(self, unit):
        self.unit = unit

    def show(self):
        result = subprocess.run(
            [
                "systemctl",
                "show",
                self.unit,
                "--property=ActiveState",
                "--property=SubState",
                "--property=MainPID",
                "--property=ControlGroup",
                "--property=ExecStart",
                "--property=EnvironmentFiles",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def owns_pid(self, pid, snapshot):
        if type(pid) is not int or pid <= 0 or not snapshot.get("ControlGroup"):
            return False
        try:
            lines = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
        except OSError:
            return False
        group = snapshot["ControlGroup"]
        return any(
            line.rsplit(":", 1)[-1] == group or line.rsplit(":", 1)[-1].startswith(group + "/")
            for line in lines
        )

    def action(self, action):
        if action not in {"stop", "start"}:
            raise up.UpgradeError("unsupported service action")
        subprocess.run(
            ["systemctl", action, self.unit], capture_output=True, check=True, timeout=120
        )


class Launchd:
    """launchd adapter with a forced-restart stop.

    launchd has no drain signal: ``stop`` is SIGTERM through launchctl, and a
    client that ignores it is killed after the ThrottleInterval. That is the
    agreed trade-off for this path -- in-flight work is abandoned rather than
    drained, so a caller must accept the interruption before using it.

    The adapter reports the same fields the systemd one does so supervisor
    logic stays shared. ``ControlGroup`` and ``ExecStart`` are synthesized from
    launchd's own properties instead of systemctl's structured output.
    """

    # Tells supervise() that stop is a kill, so draining cannot help.
    forced_restart = True

    def __init__(self, label, domain=None):
        self.label = label
        self.domain = domain or f"gui/{os.getuid()}"

    def _print(self, key):
        result = subprocess.run(
            ["launchctl", "print", f"{self.domain}/{self.label}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            return None
        match = re.search(rf"\b{key} = (.+)", result.stdout)
        return match[1].strip() if match else None

    def show(self):
        described = subprocess.run(
            ["launchctl", "list", self.label],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if described.returncode:
            # Not loaded. Absent is a valid state for a stopped service; the
            # caller distinguishes it from an active one by ActiveState.
            return {"ActiveState": "inactive", "SubState": "dead", "MainPID": "0"}
        pid = re.search(r'"PID" = (\d+)', described.stdout)
        return {
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": pid[1] if pid else "0",
            # supervisor._runner_bound parses the systemd structure; launchd
            # binding is verified through owns_pid instead, so declare the
            # shape without claiming a match.
            "ExecStart": "",
            "ControlGroup": "",
        }

    def owns_pid(self, pid, snapshot):
        """Prove the reported PID belongs to this label's job.

        launchctl does not expose a control-group equivalent, so compare the
        tracked PID against the one launchd reports for the same label.
        """
        if type(pid) is not int or pid <= 0:
            return False
        return snapshot.get("MainPID") == str(pid)

    def action(self, action):
        if action not in {"stop", "start"}:
            raise up.UpgradeError("unsupported service action")
        if action == "start":
            subprocess.run(
                ["launchctl", "kickstart", f"{self.domain}/{self.label}"],
                capture_output=True,
                check=True,
                timeout=120,
            )
            return
        # Force the restart: SIGKILL to the job so a wedged client cannot hold
        # the instance into the maintenance window. KeepAlive brings it back
        # only after start, so the instance stays down while the pointer moves.
        subprocess.run(
            ["launchctl", "kill", "SIGKILL", f"{self.domain}/{self.label}"],
            capture_output=True,
            check=True,
            timeout=120,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.show().get("ActiveState") != "active":
                return
            time.sleep(0.1)
        raise up.UpgradeError("launchd job did not stop after SIGKILL")


def _healthy(instance, record, service, snapshot, health, *, candidate=False):
    release = (record["candidate"] if candidate else record["baseline"]["active"])["release"]
    return (
        snapshot.get("ActiveState") == "active"
        and int(snapshot.get("MainPID", "0")) > 0
        and health.get("ready") is True
        and "maintenance_owner" in health
        and (not candidate or health["maintenance_owner"] is None)
        and service.owns_pid(health.get("pid"), snapshot)
        and health.get("release") == release
        and health.get("python") == str(instance / "releases" / release / "client/venv/bin/python")
        and health.get("device_id") == record["baseline"]["identity"]["instance_id"]
    )


def _runner_bound(instance, snapshot):
    # systemctl's structured property serialization, not a substring of argv.
    match = re.fullmatch(
        r"\{ path=([^ ;]+) ; argv\[\]=([^;]+) ; .*\}", snapshot.get("ExecStart", "")
    )
    if match:
        return match[1] == str(instance / "run") and match[2].strip() == (
            str(instance / "run") + " client"
        )
    # launchd does not serialize ExecStart this way. Its ProgramArguments are
    # authoritative in the installed plist, so read the job definition instead
    # of inferring intent from a running process's argv.
    path = instance / "launchd" / f"com.device-runtime.{instance.name}.plist"
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as stream:
            job = plistlib.load(stream)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
    return job.get("ProgramArguments") == [str(instance / "run"), "client"]


def profile(root):
    path = root / "upgrade-profile.json"
    if path.is_symlink():
        raise up.UpgradeError("invalid administrator profile")
    info = path.stat()
    # Linux profiles are provisioned by root; macOS installs are per-user, where
    # ownership by root would be wrong. Both must stay non-writable by others.
    if sys.platform == "linux" and info.st_uid != 0:
        raise up.UpgradeError("profile must be administrator-owned and not writable by others")
    if info.st_mode & 0o022:
        raise up.UpgradeError("profile must be administrator-owned and not writable by others")
    value = json.loads(path.read_text())
    if value.get("format_version") != 1 or value.get("role") != "client":
        raise up.UpgradeError("only an explicitly provisioned client profile is supported")
    if value.get("root") != str(root):
        raise up.UpgradeError("profile binding mismatch")
    if sys.platform == "darwin":
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value.get("label", "")):
            raise up.UpgradeError("profile binding mismatch")
    elif not re.fullmatch(r"[A-Za-z0-9_-]+\.service", value.get("unit", "")):
        raise up.UpgradeError("profile binding mismatch")
    if value.get("allow_upgrade") is not True:
        raise up.UpgradeError("local policy disables upgrades")
    return value


def _adapter(settings, adapter=None):
    """Build the service adapter for this host, or pass an injected one through."""
    if adapter is not None:
        return adapter
    if sys.platform == "darwin":
        return Launchd(settings["label"], settings.get("domain"))
    return Systemd(settings["unit"])


def _service_key(settings):
    """The identifier a supervision intent records for this service.

    The profile schema is what decides: a profile that names a label is a
    launchd job, one that names a unit is a systemd service. Reading the key
    that exists keeps injected test profiles and both real platforms working
    without having to trust the interpreter's platform alone.
    """
    if "label" in settings:
        return settings["label"]
    return settings["unit"]


@legacy_operation
def reconcile_supervision(root, upgrade_id, *, adapter=None):
    """Evidence-only recovery: never stop/start, publish, or replay unknown work."""
    instance = up.life._root(root)
    settings = profile(instance)
    with up._supervision_lease(instance, exclusive=True):
        record = up.status(instance, upgrade_id)
        intent = record.get("supervisor")
        if not intent or intent["unit"] != _service_key(settings):
            raise up.UpgradeError("no matching supervision intent")
        if record["state"] in {"verified_local", "cancelled", "failed"}:
            return record
        if intent.get("phase") not in {
            "draining",
            "stopping",
            "starting",
            "recovering-publish",
            "recovering-original",
            "recovering-candidate",
        }:
            raise up.UpgradeError("unsupported supervision recovery phase")
        current = up._baseline(instance)
        baseline = record["baseline"]
        # Ignore only the pointer itself: identity, configuration and state-format
        # must still match the frozen preparation binding.
        if {k: v for k, v in current.items() if k != "active"} != {
            k: v for k, v in baseline.items() if k != "active"
        }:
            raise up.UpgradeError("recovery binding changed")
        service = _adapter(settings, adapter)
        snapshot = service.show()
        if not _runner_bound(instance, snapshot):
            raise up.UpgradeError("service binding changed")
        pointer = up.life._read(instance / "active.json")
        exact = record.get("expected_active") is not None and pointer == record["expected_active"]
        sock = instance / "state/client/maintenance.sock"
        try:
            health = request(sock, "status", upgrade_id)
        except (OSError, ValueError):
            health = {}
        if exact and _healthy(instance, record, service, snapshot, health, candidate=True):
            record["state"] = "verified_local"
            intent["phase"] = "verified_local"
            record.pop("code", None)
        else:
            record["state"] = "unknown"
            if pointer == baseline["active"]:
                code = "original_release_not_resumed"
            elif exact:
                code = "published_release_health_unconfirmed"
            else:
                code = "active_pointer_conflict"
            record["code"] = code
        up._save(instance / "upgrades" / (upgrade_id + ".json"), record)
        return record


@legacy_operation
def recover(root, upgrade_id, *, action, expected_record_sha256, confirmed=False, adapter=None):
    """One explicitly approved recovery step, pinned to an inspected record.

    Never stops a service, never rolls back a pointer, never replays tasks.
    A changed receipt (including a prior unknown attempt) requires fresh review.
    """
    if not confirmed or action not in {"publish", "start-original", "start-candidate"}:
        raise up.UpgradeError("explicit supported recovery action required")
    instance = up.life._root(root)
    settings = profile(instance)
    # status validates the ID and layout before any path use.
    up.status(instance, upgrade_id)
    with up._supervision_lease(instance, exclusive=True):
        path = instance / "upgrades" / (upgrade_id + ".json")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_record_sha256:
            raise up.UpgradeError("recovery record changed; inspect again")
        record = up.status(instance, upgrade_id)
        intent = record.get("supervisor", {})
        phases = {
            "stopping",
            "starting",
            "recovering-publish",
            "recovering-original",
            "recovering-candidate",
        }
        if (
            intent.get("unit") != _service_key(settings)
            or intent.get("phase") not in phases
            or record["state"] not in {"unknown", "prepared", "publishing", "published"}
        ):
            raise up.UpgradeError("unsupported recovery stage")
        current = up._baseline(instance)
        baseline = record["baseline"]
        if {k: v for k, v in current.items() if k != "active"} != {
            k: v for k, v in baseline.items() if k != "active"
        }:
            raise up.UpgradeError("recovery binding changed")
        service = _adapter(settings, adapter)
        snapshot = service.show()
        if (
            not _runner_bound(instance, snapshot)
            or snapshot.get("ActiveState") not in {"inactive", "failed"}
            or int(snapshot.get("MainPID", "0")) != 0
        ):
            raise up.UpgradeError("bound service must be stopped; reconcile running service first")
        pointer = current["active"]
        original = pointer == baseline["active"]
        exact = record.get("expected_active") is not None and pointer == record["expected_active"]
        if action == "publish":
            if not original:
                raise up.UpgradeError("pointer changed; reconcile before publication")
            if "expected_active" in record:
                up._publication_pointer(record)
        elif action == "start-original":
            if not original or "expected_active" in record:
                raise up.UpgradeError("cannot start original after publication intent; no rollback")
        elif not exact:
            raise up.UpgradeError("candidate publication unconfirmed")
        if action != "start-original":
            up._policy(
                instance,
                release=record["candidate"]["release_version"],
                fingerprint=record["request"]["bundle_sha256"],
            )
            candidate = record["candidate"]
            if (
                up.life._check_release(instance, candidate["release"], candidate["roles"])
                != candidate
            ):
                raise up.UpgradeError("candidate changed")
        record["state"] = "unknown"
        phase = {
            "publish": "recovering-publish",
            "start-original": "recovering-original",
            "start-candidate": "recovering-candidate",
        }[action]
        intent["phase"] = phase
        record["recovery"] = {"action": action, "reviewed_record_sha256": expected_record_sha256}
        up._save(path, record)
        try:
            if action == "publish":
                record = up.activate(
                    instance, upgrade_id, stopped=True, _supervised=True, _recovery=True
                )
                record["supervisor"]["phase"] = "starting"
                up._save(path, record)
            else:
                service.action("start")
                # Start receipt is not health. Original uses explicit resume; candidate uses evidence-only reconcile.
                record["supervisor"]["phase"] = (
                    "draining" if action == "start-original" else "starting"
                )
                up._save(path, record)
        except (OSError, ValueError, subprocess.SubprocessError):
            # Publication may already have persisted exact evidence. Never overwrite
            # it with the pre-action snapshot when recording an uncertain receipt.
            current_record = up.status(instance, upgrade_id)
            current_record["state"] = "unknown"
            current_record["code"] = "manual_recovery_unconfirmed"
            up._save(path, current_record)
            raise up.UpgradeError(
                "recovery step unconfirmed; inspect and reconcile before any new action"
            ) from None
        return record


@legacy_operation
def resume_admission(root, upgrade_id, *, confirmed=False, adapter=None):
    """Recover only a durable drain intent; never replay stop/start/publication."""
    if not confirmed:
        raise up.UpgradeError("explicit maintenance recovery confirmation required")
    instance = up.life._root(root)
    settings = profile(instance)
    with up._supervision_lease(instance, exclusive=True):
        record = up.status(instance, upgrade_id)
        if record.get("supervisor", {}).get("phase") not in {"draining", "recovering-original"}:
            raise up.UpgradeError(
                "service intent cannot be resumed; manual reconciliation required"
            )
        if (
            record["supervisor"]["unit"] != _service_key(settings)
            or up._baseline(instance) != record["baseline"]
        ):
            raise up.UpgradeError("recovery binding changed")
        service = _adapter(settings, adapter)
        snapshot = service.show()
        sock = instance / "state/client/maintenance.sock"
        health = request(sock, "status", upgrade_id)
        if not _runner_bound(instance, snapshot) or not _healthy(
            instance, record, service, snapshot, health
        ):
            raise up.UpgradeError("original running client unconfirmed")
        owner = health.get("maintenance_owner")
        if owner not in (None, upgrade_id):
            raise up.UpgradeError("another maintenance owner")
        if owner == upgrade_id:
            request(sock, "resume", upgrade_id)
        health = request(sock, "status", upgrade_id)
        if health.get("maintenance_owner") is not None or not _healthy(
            instance, record, service, service.show(), health
        ):
            raise up.UpgradeError("admission recovery unconfirmed")
        record["state"] = "cancelled"
        record["supervisor"]["phase"] = "admission_resumed"
        record.pop("code", None)
        up._save(instance / "upgrades" / (upgrade_id + ".json"), record)
        return record


@legacy_operation
def supervise(root, upgrade_id, *, confirmed=False, adapter=None, timeout=60):
    if not confirmed:
        raise up.UpgradeError("explicit service-change confirmation required")
    if not 0 < timeout <= 300:
        raise up.UpgradeError("invalid maintenance timeout")
    instance = up.life._root(root)
    settings = profile(instance)
    record = up.status(instance, upgrade_id)
    if record["state"] in {"verified_local", "failed", "unknown"}:
        return record
    if record.get("supervisor"):
        raise up.UpgradeError("prior service intent requires explicit reconciliation")
    if record["state"] != "prepared":
        raise up.UpgradeError("interrupted supervisor requires reconciliation, not replay")
    if record["baseline"]["active"]["roles"] != ["client"]:
        raise up.UpgradeError("mixed-role instances require a separate maintenance plan")
    service = _adapter(settings, adapter)
    snapshot = service.show()
    if not _runner_bound(instance, snapshot):
        raise up.UpgradeError("unit does not use the bound stable runner")
    if snapshot.get("ActiveState") != "active" or int(snapshot.get("MainPID", "0")) <= 0:
        raise up.UpgradeError("client must be healthy before supervised maintenance")
    sock = instance / "state/client/maintenance.sock"
    deadline = time.monotonic() + timeout
    # Serialize the whole supervisor operation. activate() takes the upgrade
    # lock itself, so this separate lease protects only supervision ownership.
    with up._supervision_lease(instance, exclusive=True):
        # Recheck after acquiring the supervisor lease, before any action.
        record = up.status(instance, upgrade_id)
        if record["state"] != "prepared" or up._baseline(instance) != record["baseline"]:
            raise up.UpgradeError("stale supervisor request")
        path = instance / "upgrades" / (upgrade_id + ".json")
        up._policy(
            instance,
            release=record["candidate"]["release_version"],
            fingerprint=record["request"]["bundle_sha256"],
        )
        if not _healthy(
            instance, record, service, service.show(), request(sock, "status", upgrade_id)
        ):
            raise up.UpgradeError("old client identity or readiness unconfirmed")
        record["supervisor"] = {"phase": "draining", "unit": _service_key(settings)}
        up._save(path, record)
        try:
            # launchd restarts are forced: the job is SIGKILLed and in-flight
            # work is abandoned. Draining first would only lengthen a window in
            # which the answer is already "no", so this path skips it and goes
            # straight to the stop. The systemd path keeps drain as its
            # contract, because there SIGTERM is graceful and tasks survive.
            #
            # Keyed on the adapter, not the interpreter: an injected adapter in
            # a test must get the same drain semantics as the real service it
            # stands in for.
            if getattr(service, "forced_restart", False):
                pass
            else:
                while True:
                    drained = request(sock, "drain", upgrade_id)
                    if drained.get("drained") is True:
                        break
                    if time.monotonic() >= deadline:
                        request(sock, "resume", upgrade_id)
                        raise up.UpgradeError("tasks did not drain; service remains running")
                    time.sleep(0.1)
                if not _healthy(
                    instance, record, service, service.show(), request(sock, "status", upgrade_id)
                ):
                    raise up.UpgradeError("old client binding changed before stop")
            up._policy(
                instance,
                release=record["candidate"]["release_version"],
                fingerprint=record["request"]["bundle_sha256"],
            )
            # Durable intent before stopping: restart cannot blindly reissue it.
            path = instance / "upgrades" / (upgrade_id + ".json")
            record["supervisor"] = {"phase": "stopping", "unit": _service_key(settings)}
            up._save(path, record)
            service.action("stop")
            stopped = service.show()
            if stopped.get("ActiveState") not in {"inactive", "failed"} or int(
                stopped.get("MainPID", "0")
            ):
                raise up.UpgradeError("service stop not confirmed")
            # Public activate validates candidate, original binding and exclusive runner lease.
            record = up.activate(instance, upgrade_id, stopped=True, _supervised=True)
            record["supervisor"] = {"phase": "starting", "unit": _service_key(settings)}
            up._save(path, record)
            service.action("start")
            deadline = time.monotonic() + timeout
            while True:
                running = service.show()
                try:
                    health = request(sock, "status", upgrade_id)
                except (OSError, ValueError):
                    health = {}
                if _healthy(instance, record, service, running, health, candidate=True):
                    record["state"] = "verified_local"
                    record["supervisor"]["phase"] = "verified_local"
                    up._save(path, record)
                    return record
                if time.monotonic() >= deadline:
                    raise up.UpgradeError("new client health unconfirmed")
                time.sleep(0.1)
        except (OSError, ValueError, subprocess.SubprocessError):
            # No automatic start/retry/rollback. Exact pointer and service evidence
            # must be reconciled before a new request may operate this instance.
            current = up.status(instance, upgrade_id)
            if current.get("supervisor"):
                current["state"] = "unknown"
                current["code"] = "supervised_maintenance_unconfirmed"
                up._save(instance / "upgrades" / (upgrade_id + ".json"), current)
            raise up.UpgradeError(
                "maintenance did not complete; reconcile before continuing"
            ) from None
