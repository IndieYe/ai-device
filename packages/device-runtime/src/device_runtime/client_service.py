"""Small systemd adapter for the single installed Linux client."""

import subprocess
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")

from .maintenance import idle_lease

UNIT = "ai-device-client.service"


class ServiceError(RuntimeError):
    pass


class SystemdClient:
    def __init__(self, *, run=subprocess.run):
        self.run = run

    def _call(self, *arguments):
        result = self.run(
            ["systemctl", *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
        )
        if result.returncode:
            # Do not propagate arbitrary systemctl output into installer logs.
            raise ServiceError("systemd operation failed; inspect the client service")
        return result.stdout

    def validate_registration(self):
        fragment = self._call("show", UNIT, "--property=FragmentPath", "--value").strip()
        if fragment != "/etc/systemd/system/ai-device-client.service":
            raise ServiceError("client service comes from an unexpected location")
        # Drop-ins can change execution or shutdown policy. This small installer
        # does not merge administrator overrides; reject rather than guess.
        if self._call("show", UNIT, "--property=DropInPaths", "--value").strip():
            raise ServiceError("client service has unsupported overrides")
        if self._call("show", UNIT, "--property=SendSIGKILL", "--value").strip() != "no":
            raise ServiceError("client service permits forced termination")
        if self._call("show", UNIT, "--property=KillMode", "--value").strip() != "control-group":
            raise ServiceError("client service does not stop the complete process group")

    def validate_config(self):
        user = self._call("show", UNIT, "--property=User", "--value").strip()
        if not user:
            raise ServiceError("client service user is not explicit")
        result = self.run(
            [
                "runuser",
                "-u",
                user,
                "--",
                "/opt/ai-device-client/runtime/bin/python3",
                "-I",
                "-B",
                "-c",
                (
                    "from device_runtime.client_config import load; "
                    "a,t=load('/etc/ai-device-client/client.json'); "
                    "assert str(a.state_dir)=='/var/lib/ai-device-client'"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if result.returncode:
            raise ServiceError("configuration is not usable by the service user")

    def state(self):
        text = self._call(
            "show", UNIT, "--property=LoadState,ActiveState,SubState,MainPID,ControlPID"
        )
        values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        if values.get("LoadState") not in ("loaded", "not-found"):
            raise ServiceError("client service definition unavailable")
        try:
            main_pid = int(values["MainPID"])
            control_pid = int(values["ControlPID"])
        except (KeyError, ValueError) as exc:
            raise ServiceError("client service process state unknown") from exc
        active = values.get("ActiveState")
        if active in ("inactive", "failed") and main_pid == control_pid == 0:
            self.confirm_empty_group()
            return "stopped"
        if (
            values.get("LoadState") == "loaded"
            and active == "active"
            and values.get("SubState") == "running"
            and main_pid > 0
            and control_pid == 0
        ):
            return "running"
        raise ServiceError("client service is transitioning or its state is unknown")

    def confirm_empty_group(self):
        # MainPID/ControlPID can both be zero while timed-out descendants remain.
        group = self._call("show", UNIT, "--property=ControlGroup", "--value").strip()
        if not group:
            return
        if not group.startswith("/") or ".." in Path(group).parts:
            raise ServiceError("client process group is unknown")
        directory = CGROUP_ROOT / group.lstrip("/")
        if not directory.exists():
            return
        try:
            events = dict(
                line.split() for line in (directory / "cgroup.events").read_text().splitlines()
            )
        except (OSError, ValueError) as exc:
            raise ServiceError("cannot confirm client process group is empty") from exc
        if events.get("populated") != "0":
            raise ServiceError("client process group still has live processes")

    def enable(self):
        # Persist across reboots without starting: registration and start are
        # separate operator decisions.
        self._call("daemon-reload")
        self._call("enable", UNIT)

    def stop_for_install(self, socket_path):
        """Return only after verified stop; do not replace files in this layer."""
        if self.state() == "stopped":
            return
        with idle_lease(socket_path):
            # Holding admission prevents new work while systemd performs normal
            # shutdown. Unit configuration must disable final SIGKILL escalation.
            self._call("stop", UNIT)
            if self.state() != "stopped":
                raise ServiceError("client did not stop; package replacement forbidden")

    def start(self):
        self._call("daemon-reload")
        self.validate_registration()
        # Explicit installation repair must clear a previous failed version's
        # start-limit counter; this does not introduce automatic retry loops.
        self._call("reset-failed", UNIT)
        self._call("start", UNIT)
        if self.state() != "running":
            raise ServiceError("client service did not start")
        # Running is not readiness. The installer must still probe the client's
        # local status and verify the installed software identity separately.


def render_unit(user):
    """Single-client Linux layout; user is explicit, never guessed from sudo."""
    import re

    if not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        raise ValueError("an explicit local service user is required")
    return f"""[Unit]
Description=AI Device client
After=network.target
ConditionPathExists=/etc/ai-device-client/client.json
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
User={user}
ExecStart=/opt/ai-device-client/runtime/bin/python3 -I -m device_runtime.client_cli --config /etc/ai-device-client/client.json
WorkingDirectory=/var/lib/ai-device-client
StateDirectory=ai-device-client
StateDirectoryMode=0700
LogsDirectory=ai-device-client
LogsDirectoryMode=0700
Environment=PYTHONUNBUFFERED=1
UMask=0077
Restart=on-failure
RestartSec=10
KillMode=control-group
TimeoutStopSec=30
SendSIGKILL=no
StandardOutput=append:/var/log/ai-device-client/client.log
StandardError=append:/var/log/ai-device-client/client.log

[Install]
WantedBy=multi-user.target
"""
