"""Explicit single-user macOS client layout and LaunchAgent definition.

Pure rendering only: this module never registers, loads or stops a service.
The installer must prove quiescence before bootout; this plist alone does not
make bootout of a running job safe.
"""

import plistlib
from dataclasses import dataclass
from pathlib import Path

LABEL = "local.ai-device.client"
PREFIX = Path("/opt/ai-device-client")
PYTHON = PREFIX / "runtime/bin/python3"
LAUNCHER = PREFIX / "bin/ai-device-client-launcher"


@dataclass(frozen=True)
class MacClientLayout:
    """Identity resolved from an explicitly selected account, never root HOME."""

    uid: int
    home: Path

    def __post_init__(self):
        if type(self.uid) is not int or self.uid <= 0:
            raise ValueError("macOS client requires an explicit ordinary user")
        if not isinstance(self.home, Path) or not self.home.is_absolute():
            raise ValueError("macOS client requires an absolute account home")
        if self.home in (Path("/"), Path("/var/root")) or ".." in self.home.parts:
            raise ValueError("macOS client requires an ordinary account home")

    @property
    def directory(self):
        return self.home / "Library/Application Support/ai-device-client"

    @property
    def config(self):
        return self.directory / "client.json"

    @property
    def token(self):
        return self.directory / "token"

    @property
    def state(self):
        return self.directory / "state"

    @property
    def log(self):
        return self.home / "Library/Logs/ai-device-client/client.log"

    @property
    def plist(self):
        return self.home / "Library/LaunchAgents" / f"{LABEL}.plist"

    @property
    def domain(self):
        return f"gui/{self.uid}"

    @property
    def service(self):
        return f"{self.domain}/{LABEL}"

    def definition(self):
        return {
            "Label": LABEL,
            "ProgramArguments": [
                str(LAUNCHER),
            ],
            "RunAtLoad": True,
            # A deliberate, successful TERM exit must not trigger KeepAlive.
            # No PathState semaphore: launchd documents its observation races.
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 30,
            # Do not kill leftover children when the main process exits. The
            # installer must independently prove absence of descendants.
            "AbandonProcessGroup": True,
            "ProcessType": "Background",
            "Umask": 0o077,
            "StandardOutPath": str(self.log),
            "StandardErrorPath": str(self.log),
        }

    def render(self):
        return plistlib.dumps(self.definition(), fmt=plistlib.FMT_XML, sort_keys=True)
