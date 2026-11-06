"""Register the installed client service for an explicitly chosen user.

Runs as root, after configuration and before the first start. The service user
is never inferred from sudo, the current login, or an existing unit: root is a
deliberate administrator choice, not a default. Registration writes the unit,
gives the runtime and credential files to that user, and enables the unit; it
does not start the service.
"""

import argparse
import grp
import json
import os
import pwd
import stat
import subprocess
from pathlib import Path

CONFIG_DIR = Path("/etc/ai-device-client")
CONFIG = CONFIG_DIR / "client.json"
UNIT_PATH = Path("/etc/systemd/system/ai-device-client.service")
UNIT_NAME = "ai-device-client.service"
STATE_DIR = Path("/var/lib/ai-device-client")
LOG_DIR = Path("/var/log/ai-device-client")
PROGRAM = Path("/opt/ai-device-client")


def resolve_user(name):
    """Return (pw_entry, primary_group_name) or raise for an unusable account."""
    if not isinstance(name, str) or not name:
        raise ValueError("an explicit service user is required")
    if name.startswith("-") or any(c.isspace() or ord(c) < 32 for c in name):
        raise ValueError("an explicit service user is required")
    try:
        account = pwd.getpwnam(name)
    except KeyError:
        raise ValueError("service user does not exist on this host") from None
    if account.pw_uid == 0 and name != "root":
        raise ValueError("service user is ambiguous")
    if not Path(account.pw_dir).is_absolute():
        raise ValueError("service user home is not absolute")
    try:
        group = grp.getgrgid(account.pw_gid).gr_name
    except KeyError:
        raise ValueError("service user primary group is unknown") from None
    return account, group


def check_install():
    if os.geteuid() != 0:
        raise PermissionError("registration requires root")
    if not (PROGRAM / "runtime/bin/python3").is_file():
        raise ValueError("installed program is incomplete; reinstall the package")
    if not CONFIG.is_file():
        raise ValueError("client configuration is missing; run setup first")
    if UNIT_PATH.is_symlink() or (UNIT_PATH.exists() and not UNIT_PATH.is_file()):
        raise ValueError("client service definition is not a regular file")


def service_active():
    """Report whether the installed unit is running, refusing an unknown answer."""
    result = subprocess.run(
        ["systemctl", "is-active", UNIT_NAME],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    state = result.stdout.strip()
    if state in ("active", "inactive", "failed"):
        return state == "active"
    raise ValueError("client service state is unknown; the running user is unchanged")


def _require_owner(info, account):
    # systemd creates these directories as the unit user; a different owner means
    # another identity already holds the runtime, which registration must not take.
    if info.st_uid not in (0, account.pw_uid):
        raise ValueError("runtime path is owned by another user")


def adopt_directories(account):
    """Give the runtime directories to the service user without widening modes."""
    for path in (STATE_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
        _adopt_tree(path, account)


def _adopt_tree(root, account):
    """Align existing contents so a user change cannot leave unreadable state."""
    pending = [root]
    while pending:
        current = pending.pop()
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("runtime path is not a plain directory")
        _require_owner(info, account)
        os.chown(current, account.pw_uid, account.pw_gid)
        os.chmod(current, 0o700)
        for entry in current.iterdir():
            info = entry.lstat()
            if stat.S_ISDIR(info.st_mode):
                pending.append(entry)
            elif stat.S_ISREG(info.st_mode):
                _require_owner(info, account)
                os.chown(entry, account.pw_uid, account.pw_gid)
            else:
                raise ValueError("runtime directory contains an unsupported entry")


def adopt_credentials(account):
    """The service user must be able to read the token the service loads."""
    try:
        value = json.loads(CONFIG.read_text())
        token_path = Path(value["token_file"])
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("client configuration is unreadable; run setup again") from None
    if token_path.parent != CONFIG_DIR:
        raise ValueError("client token is outside the configuration directory")
    if token_path.is_symlink() or not token_path.is_file():
        raise ValueError("client token is not a regular file")
    _require_owner(token_path.lstat(), account)
    os.chown(token_path, account.pw_uid, account.pw_gid)
    os.chmod(token_path, 0o600)


def _refuse_running_user_change(account):
    """Never hand a running service to a different user behind the operator."""
    if not UNIT_PATH.is_file():
        return
    current = None
    for line in UNIT_PATH.read_text().splitlines():
        if line.startswith("User="):
            current = line.split("=", 1)[1].strip()
            break
    if current is None or current == account.pw_name:
        return
    if service_active():
        raise ValueError(
            "client service is running as a different user; "
            "stop it explicitly before changing the service user"
        )


def register(user):
    account, _ = resolve_user(user)
    check_install()
    from .client_management import management_lock

    unit = render_unit(account.pw_name)

    with management_lock(package_script=False):
        from .client_conflicts import check_conflicts
        from .client_service import SystemdClient

        check_conflicts()
        _refuse_running_user_change(account)
        adopt_directories(account)
        adopt_credentials(account)
        CONFIG_DIR.chmod(0o755)
        os.chmod(CONFIG, 0o644)
        temporary = UNIT_PATH.with_name(UNIT_PATH.name + ".new")
        temporary.write_text(unit)
        temporary.chmod(0o644)
        os.replace(temporary, UNIT_PATH)
        SystemdClient().enable()
    return account.pw_name, "root" if account.pw_uid == 0 else "user"


def render_unit(user):
    from .client_service import render_unit as render

    return render(user)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="explicit service user name")
    args = parser.parse_args(argv)
    try:
        name, kind = register(args.user)
    except PermissionError:
        parser.exit(1, "registration requires root\n")
    except ValueError as exc:
        parser.exit(1, f"registration refused: {exc}\n")
    except RuntimeError as exc:
        parser.exit(1, f"registration failed: {exc}\n")
    except OSError:
        parser.exit(1, "registration failed; inspect service state and paths\n")
    if kind == "root":
        print(
            f"Registered: {name}. Device commands will run with root privileges. "
            "Start is still required."
        )
    else:
        print(f"Registered: {name}. Start is still required.")


if __name__ == "__main__":
    main()
