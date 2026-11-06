"""Registered upgrade window: unload the user service, swap the program, reload.

Requirement 6.3/7.3: while the user is logged in, a replacement must not leave
the old program loaded, because `KeepAlive` may restart it inside the window.

Ordering that this module enforces, under one continuously held management lock:

1. `stop`: the running instance is asked to exit over its own idle lease
   connection and its full exit is confirmed (the caller supplies that
   evidence via `confirm_stopped`; this module does not signal anything).
2. `unload`: the exact registered job is booted out, so no restart source
   remains during the swap.
3. `swap`: the program tree is replaced by the caller-supplied publication.
4. `load`: the job is bootstrapped again. `bootstrap` both loads the definition
   and honours `RunAtLoad`, so it is the single start request; no separate
   `kickstart` is issued and no window exists where the job is loaded but idle.

Every step is one attempt. A failure after the running instance was stopped
leaves the job unloaded and reports an explicit unknown/incomplete result; it
never retries, never rolls back to the old program and never claims readiness.
"""

import os
import sys
from contextlib import contextmanager

from .client_macos_service_job import (
    MacServiceJobError,
    load_registered_service,
    unload_registered_service,
)


class MacUpgradeWindowError(RuntimeError):
    pass


@contextmanager
def _management_scope(lock_factory, matches):
    """Hold management exclusively for the whole window; never re-entrant."""

    with lock_factory() as descriptor:
        if matches(descriptor) is not True:
            raise MacUpgradeWindowError("management lock is unavailable")
        yield descriptor


def replace_registered_program(
    *,
    confirm_stopped,
    publish,
    verify_ready,
    lock_factory=None,
    matches=None,
    load_run=None,
    unload_run=None,
):
    """Stop, unload, swap and reload the registered client job.

    `confirm_stopped()` must return True only when the previously running
    instance has fully exited and cross-instance start protection is held.
    `publish()` performs the program replacement and raises on any doubt.
    `verify_ready()` runs after the reload; its result is returned verbatim.
    """
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise MacUpgradeWindowError("registered upgrade requires macOS administrator")
    for name, value in (
        ("confirm_stopped", confirm_stopped),
        ("publish", publish),
        ("verify_ready", verify_ready),
    ):
        if not callable(value):
            raise MacUpgradeWindowError(f"{name} callback is required")
    if matches is None:
        from .client_macos_management import management_lock, management_lock_matches

        lock_factory = lock_factory or management_lock
        matches = management_lock_matches
    elif lock_factory is None:
        raise MacUpgradeWindowError("lock factory is required with custom matches")

    stages = []
    with _management_scope(lock_factory, matches) as descriptor:

        def guard():
            return matches(descriptor) is True

        if confirm_stopped() is not True:
            raise MacUpgradeWindowError(
                "running instance not confirmed stopped; program replacement refused"
            )
        stages.append("stopped")

        try:
            unload_registered_service(run=unload_run, guard_current=guard)
        except MacServiceJobError as error:
            raise MacUpgradeWindowError(
                f"service unload failed before replacement; {error}"
            ) from None
        stages.append("unloaded")

        # The job stays unloaded across the swap: no KeepAlive restart source.
        publish()
        stages.append("published")

        try:
            loaded = load_registered_service(run=load_run, guard_current=guard)
        except MacServiceJobError as error:
            raise MacUpgradeWindowError(
                f"reload failed after replacement; program is replaced but not "
                f"running ({', '.join(stages)}); {error}"
            ) from None
        stages.append("loaded")

    # Readiness is observed outside the write window; reload already started it.
    ready = verify_ready()
    return {
        "stages": stages,
        "service_loaded": loaded["service_loaded"],
        "start_domain": loaded["start_domain"],
        "ready": ready,
        "rolled_back": False,
    }


def uninstall_registered_client(*, confirm_stopped, lock_factory=None, matches=None,
                                unload_run=None, remove_definition=None,
                                account_reader=None):
    """Remove the registered service; keep configuration, token and data.

    Requirement: a plain uninstall removes the service and preserves user data
    and credentials. Full data deletion is a separate, explicit operation and is
    deliberately not reachable from here.
    """
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise MacUpgradeWindowError("client uninstall requires macOS administrator")
    if not callable(confirm_stopped):
        raise MacUpgradeWindowError("confirm_stopped callback is required")
    if matches is None:
        from .client_macos_management import management_lock, management_lock_matches

        lock_factory = lock_factory or management_lock
        matches = management_lock_matches
    elif lock_factory is None:
        raise MacUpgradeWindowError("lock factory is required with custom matches")

    stages = []
    with _management_scope(lock_factory, matches) as descriptor:

        def guard():
            return matches(descriptor) is True

        if confirm_stopped() is not True:
            raise MacUpgradeWindowError(
                "running instance not confirmed stopped; uninstall refused"
            )
        stages.append("stopped")

        try:
            unload_registered_service(run=unload_run, guard_current=guard)
        except MacServiceJobError as error:
            raise MacUpgradeWindowError(
                f"service unload failed; service definition and data preserved; {error}"
            ) from None
        stages.append("unloaded")

        from .client_macos_registration import registered_account

        account = (account_reader or registered_account)()
        if not callable(remove_definition):
            raise MacUpgradeWindowError(
                "service unloaded but definition removal was not requested; "
                "reload would restore the service"
            )
        remove_definition(account.layout.plist)
        stages.append("definition_removed")

    return {
        "stages": stages,
        "service_unloaded": True,
        "definition_removed": True,
        "data_preserved": True,
    }
