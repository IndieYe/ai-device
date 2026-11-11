"""Load/unload the registered GUI LaunchAgent for one ordinary user.

This closes the missing half of the service lifecycle: registering a plist only
writes a definition, and start requests only act on an *already loaded* job.
Without bootstrap there is no loaded job at all; without bootout a replaced
program can be restarted by KeepAlive inside the replacement window.

Scope is deliberately narrow and every action is one attempt:

* Caller must already hold the management lock continuously (never reacquired
  or released here) and must re-check its guard around each action.
* Registration on disk is re-validated immediately before and after the action;
  a change makes the outcome unknown instead of "loaded"/"unloaded".
* A nonzero exit, a timeout or a missing response is UNKNOWN, never retried,
  never rolled back, never treated as the opposite state.
* Only the exact registered target is addressed. No `-k`, no `disable`, no
  process signals, no service collection sweeps, no user-command execution.
* Loading does not stop an already loaded job (`bootstrap` fails on a loaded
  label), and unloading does not prove that every descendant has exited.
"""

import os
import stat
import subprocess
import sys

from .client_macos_layout import LABEL
from .client_macos_query import run_bootout, run_bootstrap


class MacServiceJobError(RuntimeError):
    pass


def _check_administrator():
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise MacServiceJobError("service job operations require macOS administrator")


def _guard(guard_current):
    if not callable(guard_current) or guard_current() is not True:
        raise MacServiceJobError("management guard is unavailable")


def _account_and_agent():
    from .client_macos_registration import registered_account, validate_agent

    account = registered_account()
    validate_agent(account)
    return account


def _run_action(run, argv, guard_current, label, action):
    """One bounded attempt; any non-clear outcome raises a distinct error."""

    _guard(guard_current)
    try:
        result = run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise MacServiceJobError(
            f"{label} outcome unknown; do not replay automatically"
        ) from None
    _guard(guard_current)
    if result.returncode != 0:
        raise MacServiceJobError(
            f"{label} {action} refused or unknown; do not replay automatically"
        )
    return result


def load_registered_service(*, run=None, guard_current=None):
    """Bootstrap the exact registered agent; never start or replace it.

    `launchctl bootstrap` both loads the definition and honours RunAtLoad, so
    this returns without waiting for readiness: the caller must observe the
    service and its ready protocol separately.
    """
    _check_administrator()
    _guard(guard_current)
    account = _account_and_agent()
    domain = account.layout.domain
    if domain != f"gui/{account.layout.uid}":
        raise MacServiceJobError("registered start domain changed")
    _guard(guard_current)
    if _account_and_agent() != account:
        raise MacServiceJobError("registration changed before service load")
    path = str(account.layout.plist)
    _run_action(
        run or run_bootstrap,
        ["/bin/launchctl", "bootstrap", domain, path],
        guard_current,
        "service load",
        "load",
    )
    if _account_and_agent() != account:
        raise MacServiceJobError("registration changed after service load; outcome unknown")
    return {
        "service_loaded": True,
        "start_domain": domain,
        "definition": path,
        "ready_verified": False,
    }


def unload_registered_service(*, run=None, guard_current=None):
    """Bootout the exact registered agent; do not verify or force termination.

    `bootout` sends TERM and waits; a nonzero/timeout outcome is unknown. This
    function cannot and must not be used as proof that the program is fully
    stopped before replacement.
    """
    _check_administrator()
    _guard(guard_current)
    account = _account_and_agent()
    target = account.layout.service
    if target != f"gui/{account.layout.uid}/{LABEL}":
        raise MacServiceJobError("registered service target changed")
    _guard(guard_current)
    if _account_and_agent() != account:
        raise MacServiceJobError("registration changed before service unload")
    _run_action(
        run or run_bootout,
        ["/bin/launchctl", "bootout", target],
        guard_current,
        "service unload",
        "unload",
    )
    if _account_and_agent() != account:
        raise MacServiceJobError("registration changed after service unload; outcome unknown")
    return {
        "service_unloaded": True,
        "target": target,
        "stop_verified": False,
    }


def unload_then_verify_absent(*, run=None, guard_current=None, observe=None):
    """Unload, then require launchd to no longer report the exact target.

    Requirement 7.3 keeps stop evidence separate from service ownership: the
    absent target only means the job is not loaded. Callers still need their own
    process/lease evidence before replacing a running program.
    """
    from .client_macos_service_status import LaunchdStatusError, observe_service

    result = unload_registered_service(run=run, guard_current=guard_current)
    account = _account_and_agent()
    try:
        observation = (observe or observe_service)(
            account.layout, require_fixed_entry=True
        )
    except LaunchdStatusError:
        # Nonzero print is UNKNOWN, so it cannot confirm absence either.
        raise MacServiceJobError(
            "service unload outcome unknown; absence not confirmed, do not replay"
        ) from None
    if observation is not None:
        raise MacServiceJobError(
            "service still reported after unload; replacement refused"
        )
    result["stop_verified"] = True
    return result


def remove_registered_definition(plist=None, *, account=None):
    """Delete only the exact registered agent definition, keeping user data.

    Re-validates the persisted registration first so an overridden or partial
    publication is never deleted. Refuses symlinks, non-regular files and any
    path that is not the registered agent; the client's configuration, token,
    state and runtime directories are not touched.
    """
    _check_administrator()
    current = account or _account_and_agent()
    target = current.layout.plist if plist is None else plist
    if str(target) != str(current.layout.plist):
        raise MacServiceJobError("definition removal target changed")
    if account is None and _account_and_agent() != current:
        raise MacServiceJobError("registration changed before definition removal")
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return {"definition_removed": False, "already_absent": True}
    except OSError:
        raise MacServiceJobError(
            "definition removal failed; definition preserved"
        ) from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != current.layout.uid:
        raise MacServiceJobError(
            "definition removal refused: not the registered regular file"
        )
    try:
        os.unlink(target)
    except OSError:
        raise MacServiceJobError(
            "definition removal failed; definition may still be present"
        ) from None
    return {"definition_removed": True, "already_absent": False}
