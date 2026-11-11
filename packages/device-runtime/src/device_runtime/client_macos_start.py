"""Explicit no-signal start request for an already-loaded registered service.

Not an upgrade coordinator: no bootout/bootstrap, enable/disable, replacement,
start-barrier release or ready claim. Not exposed by inspection packages.
"""

import os
import subprocess
import sys

from .client_macos_management import management_lock, management_lock_matches
from .client_macos_query import run_kickstart
from .client_macos_registration import registered_account, validate_agent
from .client_macos_service_status import observe_service


class MacStartError(RuntimeError):
    pass


def request_registered_start(*, run=None, observe_run=None):
    """Request launchd start without -k; unknown outcome is never retried here."""
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise MacStartError("registered start requires macOS administrator")
    with management_lock() as descriptor:
        return _request_registered_start_held(
            guard_current=lambda: management_lock_matches(descriptor),
            run=run,
            observe_run=observe_run,
        )


def _request_registered_start_held(*, guard_current, run=None, observe_run=None):
    """Internal only; caller continuously owns management, no reacquisition.

    The callback verifies the caller's guard, not merely a remembered boolean.
    No CLI exposes this entry. Unknown action outcomes are not replayed.
    """
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise MacStartError("registered start requires macOS administrator")

    def check_guard():
        if not callable(guard_current) or guard_current() is not True:
            raise MacStartError("management guard is unavailable")

    check_guard()
    account = registered_account()
    validate_agent(account)
    observation = observe_service(account.layout, run=observe_run, require_fixed_entry=True)
    target = f"gui/{account.layout.uid}/local.ai-device.client"
    if observation.target != target:
        raise MacStartError("registered start target changed")
    check_guard()
    if registered_account() != account:
        raise MacStartError("registration changed before start")
    validate_agent(account)
    check_guard()
    try:
        result = (run or run_kickstart)(
            ["/bin/launchctl", "kickstart", target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise MacStartError(
            "registered start outcome unknown; do not replay automatically"
        ) from None
    if result.returncode != 0:
        raise MacStartError("registered start refused or unknown; do not replay automatically")
    check_guard()
    if registered_account() != account:
        raise MacStartError("registration changed after start; outcome unknown")
    validate_agent(account)
    check_guard()
    return {"start_requested": True, "ready_verified": False}
