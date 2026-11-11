"""Managed Mac client runtime entry; not a complete installation barrier.

The lifetime lock includes client_cli.main's entire asyncio.run shutdown.
Service unloading/start exclusion must still protect interpreter loading before
this module executes. The inspection PKG remains installation-disabled.
"""

import argparse
import os
import pwd
import sys
from contextlib import ExitStack

from .client_macos_account import MacAccountError, resolve_account
from .client_macos_lock import MacLockBusy, MacLockError, adopt_runtime_lock, run_or_replace_lock


def run_managed(config, *, account, invoke=None, inherited_descriptor=None):
    """Hold exclusion through main and cleanup; consume an explicit inherited fd.

    Adoption is only for the native lock-before-exec caller. It proves current
    exclusion, not provenance or interpreter loading order. No fallback on an
    invalid inherited fd: silently reopening would lose the startup guarantee.
    """
    with ExitStack() as stack:
        path = account.layout.directory / "runtime.lock"
        if inherited_descriptor is not None:
            stack.enter_context(
                adopt_runtime_lock(path, uid=account.layout.uid, descriptor=inherited_descriptor)
            )
        if account.layout.uid != os.getuid() or os.geteuid() != os.getuid():
            raise MacAccountError("managed client must run as the selected ordinary user")
        if str(config) != str(account.layout.config):
            raise MacAccountError("managed client requires the registered configuration path")
        if inherited_descriptor is None:
            stack.enter_context(run_or_replace_lock(path, uid=account.layout.uid))
        if invoke is None:
            from .client_cli import main

            def invoke(arguments):
                return main(arguments, lease_exit=True)

        invoke(["--config", str(account.layout.config)])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the registered macOS client")
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--inherit-runtime-lock",
        action="store_true",
        help="adopt fd 3 from the native lock-before-exec entry (no fallback)",
    )
    args = parser.parse_args(argv)
    pending_descriptor = 3 if args.inherit_runtime_lock else None
    try:
        if pending_descriptor is not None:
            os.set_inheritable(pending_descriptor, False)
        if sys.platform != "darwin":
            raise MacAccountError("managed Mac entry requires macOS")
        account = resolve_account(pwd.getpwuid(os.getuid()).pw_name)
        inherited_descriptor = pending_descriptor
        pending_descriptor = None  # run_managed consumes it, including failures.
        run_managed(
            args.config,
            account=account,
            inherited_descriptor=inherited_descriptor,
        )
    except MacLockBusy:
        # A replacement or another managed client owns the lock. Exit zero so
        # SuccessfulExit=false does not turn this refusal into a restart loop.
        print("client start deferred: runtime or replacement lock is held", file=sys.stderr)
        return
    except (MacAccountError, MacLockError, KeyError, OSError):
        parser.exit(2, "managed macOS client configuration or runtime lock unavailable\n")
    finally:
        if pending_descriptor is not None:
            try:
                os.close(pending_descriptor)
            except OSError:
                pass


if __name__ == "__main__":
    main()
