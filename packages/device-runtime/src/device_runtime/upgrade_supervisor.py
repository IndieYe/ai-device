"""One-shot Linux/systemd client supervisor, independent of the managed process.

Profile provisioning is an explicit administrator operation, never auto-adoption.
"""

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from . import upgrader as up
from .maintenance import request


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
    return bool(
        match
        and match[1] == str(instance / "run")
        and match[2].strip() == str(instance / "run") + " client"
    )


def profile(root):
    if sys.platform != "linux":
        raise up.UpgradeError("systemd supervision requires Linux")
    path = root / "upgrade-profile.json"
    if path.is_symlink():
        raise up.UpgradeError("invalid administrator profile")
    info = path.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise up.UpgradeError("profile must be administrator-owned and not writable by others")
    value = json.loads(path.read_text())
    if value.get("format_version") != 1 or value.get("role") != "client":
        raise up.UpgradeError("only an explicitly provisioned client profile is supported")
    if value.get("root") != str(root) or not re.fullmatch(
        r"[A-Za-z0-9_-]+\.service", value["unit"]
    ):
        raise up.UpgradeError("profile binding mismatch")
    if value.get("allow_upgrade") is not True:
        raise up.UpgradeError("local policy disables upgrades")
    return value


def reconcile_supervision(root, upgrade_id, *, adapter=None):
    """Evidence-only recovery: never stop/start, publish, or replay unknown work."""
    instance = up.life._root(root)
    settings = profile(instance)
    with up._supervision_lease(instance, exclusive=True):
        record = up.status(instance, upgrade_id)
        intent = record.get("supervisor")
        if not intent or intent["unit"] != settings["unit"]:
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
        service = adapter if adapter is not None else Systemd(settings["unit"])
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
            intent.get("unit") != settings["unit"]
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
        service = adapter if adapter is not None else Systemd(settings["unit"])
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
            if not original or "expected_active" in record:
                raise up.UpgradeError("publication already attempted or pointer changed; reconcile")
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
            record["supervisor"]["unit"] != settings["unit"]
            or up._baseline(instance) != record["baseline"]
        ):
            raise up.UpgradeError("recovery binding changed")
        service = adapter if adapter is not None else Systemd(settings["unit"])
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
    service = adapter if adapter is not None else Systemd(settings["unit"])
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
        record["supervisor"] = {"phase": "draining", "unit": settings["unit"]}
        up._save(path, record)
        try:
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
            record["supervisor"] = {"phase": "stopping", "unit": settings["unit"]}
            up._save(path, record)
            service.action("stop")
            stopped = service.show()
            if stopped.get("ActiveState") not in {"inactive", "failed"} or int(
                stopped.get("MainPID", "0")
            ):
                raise up.UpgradeError("service stop not confirmed")
            # Public activate validates candidate, original binding and exclusive runner lease.
            record = up.activate(instance, upgrade_id, stopped=True, _supervised=True)
            record["supervisor"] = {"phase": "starting", "unit": settings["unit"]}
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
