"""Register a single Mac client definition, without loading the service.

Not yet exposed by the installation-disabled inspection PKG. A registration
published into LaunchAgents can be observed at subsequent user login; callers
must treat publication as the commit point and never silently roll it back.
"""

import os
import sys

from .client_macos_account import (
    resolve_account,
    validate_config_as_user,
    validate_launcher,
    validate_registration_files,
)
from .client_macos_conflicts import check_registration_conflicts
from .client_macos_identity import MacIdentityError, read_identity, reserve_identity
from .client_macos_lock import provision_runtime_lock
from .client_macos_management import management_lock, prepare_user_directories
from .client_macos_plist import publish_agent
from .client_macos_start_gate import provision_start_gate


def register(user, *, confirm_legacy_clients_disabled=False):
    return _register(
        user, confirm_legacy_clients_disabled=confirm_legacy_clients_disabled, resume=False
    )


def resume_registration(user, *, confirm_legacy_clients_disabled=False):
    """Explicitly resume before publication; never replace an installed agent."""
    return _register(
        user, confirm_legacy_clients_disabled=confirm_legacy_clients_disabled, resume=True
    )


def _register(user, *, confirm_legacy_clients_disabled, resume):
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise ValueError("macOS registration requires administrator privileges")
    if confirm_legacy_clients_disabled is not True:
        raise ValueError("confirm custom legacy clients are stopped and auto-start disabled")
    with management_lock():
        account = resolve_account(user)
        if resume:
            identity = read_identity()
            expected = {
                "schema_version": 1,
                "user": account.name,
                "uid": account.layout.uid,
                "gid": account.gid,
                "home": str(account.layout.home),
            }
            if identity != expected:
                raise MacIdentityError("registered account changed; automatic rebinding refused")
        check_registration_conflicts(confirm_legacy_clients_disabled=True)
        validate_launcher()
        validate_registration_files(account)
        validate_config_as_user(account)
        if not resume:
            reserve_identity(account)
        prepare_user_directories(account)
        provision_runtime_lock(
            account.layout.directory / "runtime.lock", uid=account.layout.uid, gid=account.gid
        )
        provision_start_gate(account.layout.directory, uid=account.layout.uid, gid=account.gid)
        # Recheck immediately before publication. This does not prevent an
        # independent legacy administrator starting a custom service later.
        check_registration_conflicts(confirm_legacy_clients_disabled=True)
        path = publish_agent(account)
        return {"registered": True, "started": False, "uid": account.layout.uid, "plist": str(path)}


def main(argv=None):
    """Explicit administrator entry; never invoked by the inspection package."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Register one macOS client without starting it")
    parser.add_argument("action", choices=("register", "resume"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--confirm-legacy-clients-disabled", action="store_true", required=True)
    args = parser.parse_args(argv)
    operation = register if args.action == "register" else resume_registration
    try:
        result = operation(
            args.user, confirm_legacy_clients_disabled=args.confirm_legacy_clients_disabled
        )
    except (ValueError, RuntimeError, OSError):
        parser.exit(
            2,
            "macOS registration refused or incomplete; inspect registration state before retrying\n",
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
