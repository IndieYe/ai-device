"""Install and remove a per-user LaunchAgent for a macOS Device instance.

This module only writes files and calls launchctl. It never starts the client
itself, never installs a system daemon, and never runs as root: the supported
shape is one logged-in user owning one instance, matching the single-instance
deployment the delivery docs describe.

Provisioning is explicit. Nothing here adopts an existing job, guesses a label
from a running process, or enables upgrades on its own -- the upgrade profile
that allows supervised maintenance stays a separate administrator decision.
"""

import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

LABEL_PREFIX = "com.device-runtime."
DOMAIN_TEMPLATE = "gui/{uid}"


class LaunchdError(ValueError):
    """A deployment refusal or an unusable launchd environment."""


def _require_darwin():
    if sys.platform != "darwin":
        raise LaunchdError("LaunchAgent deployment requires macOS")


def label_for(instance_id):
    if not isinstance(instance_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", instance_id):
        raise LaunchdError("instance id must be a plain name")
    return LABEL_PREFIX + instance_id


def domain():
    return DOMAIN_TEMPLATE.format(uid=os.getuid())


def _plist_path(root):
    return root / "launchd" / f"{label_for(root.name)}.plist"


def _installed_path(label):
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _run(argv, *, check=True):
    result = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=60)
    if check and result.returncode:
        raise LaunchdError(f"{argv[0]} refused the request")
    return result


def build_job(root, *, label, home, path_env, env_file=None):
    """The LaunchAgent description for one instance client.

    KeepAlive is set because the whole point of the agent is to survive a
    crash; ThrottleInterval keeps a repeatedly failing client from spinning.
    PATH excludes any shell profile so launchd's minimal environment cannot
    silently pick a different interpreter.

    launchd cannot read an env file the way systemd's EnvironmentFile does, so
    credentials are passed through a wrapper whose only job is to source that
    file and exec the stable entry. The token therefore never lands in the
    plist, which is world-readable in ~/Library/LaunchAgents.
    """
    root = Path(root).absolute()
    env_file = Path(env_file).absolute() if env_file else root / "launchd" / "client.env"
    wrapper = root / "launchd" / "start.sh"
    return {
        "Label": label,
        "ProgramArguments": ["/bin/sh", str(wrapper)],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "Umask": 0o077,
        "EnvironmentVariables": {"HOME": str(home), "PATH": path_env},
        "StandardOutPath": str(root / "logs" / "client.out.log"),
        "StandardErrorPath": str(root / "logs" / "client.err.log"),
        "__env_file": str(env_file),
        "__wrapper": str(wrapper),
    }


WRAPPER = """#!/bin/sh
# Written by device-runtime. Sources the instance env file, then execs the
# stable entry so the client inherits this PID instead of a shell parent.
#
# The stable entry has two shapes: a shell launcher that resolves the release
# itself (private runtime) and a legacy Python file that needs the release as
# its first argument. The shebang decides which one this instance has, because
# the pointer is only written for the launcher form.
set -eu
umask 077
env_file={env_file}
root={root}
if [ ! -r "$env_file" ]; then
  echo "device launchd wrapper: env file is missing or unreadable" >&2
  exit 1
fi
set -a
. "$env_file"
set +a
if head -n 1 "$root/run" | grep -q '^#!/bin/sh'; then
  exec "$root/run" client
fi
release=$(cat "$root/current" 2>/dev/null || true)
if [ -z "$release" ]; then
  release=$({python} -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["release"])' "$root/active.json")
fi
exec "$root/run" "$release" client
"""


def _write_wrapper(root, env_file, wrapper, python):
    root = Path(root)
    wrapper.parent.mkdir(mode=0o700, exist_ok=True)
    wrapper.write_text(
        WRAPPER.format(env_file=env_file, root=root, python=python), encoding="utf-8"
    )
    wrapper.chmod(0o700)


def install(root, *, label=None, path_env=None, env_file=None, dry_run=False):
    """Write the wrapper and plist, then load the job."""
    _require_darwin()
    root = Path(root).absolute()
    if not (root / "run").is_file():
        raise LaunchdError("instance has no stable run entry")
    if not (root / "config" / "roles.json").is_file():
        raise LaunchdError("instance has no persistent role configuration")
    label = label or label_for(root.name)
    if not label.startswith(LABEL_PREFIX) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", label
    ):
        raise LaunchdError("label must be a device-runtime LaunchAgent name")
    (root / "logs").mkdir(mode=0o700, exist_ok=True)
    home = Path.home()
    path_env = path_env or f"{home}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    job = build_job(root, label=label, home=home, path_env=path_env, env_file=env_file)
    env_path, wrapper = job.pop("__env_file"), job.pop("__wrapper")
    installed = _installed_path(label)
    if dry_run:
        return {
            "status": "dry-run",
            "label": label,
            "plist": str(installed),
            "env_file": env_path,
            "job": job,
        }
    if not Path(env_path).is_file():
        raise LaunchdError(
            "instance has no launchd env file; create it with the device token first"
        )
    # The legacy entry needs an interpreter to read active.json, and launchd's
    # PATH is not the user's shell PATH. Pin the interpreter that installed the
    # instance so the wrapper never depends on discovery.
    python = sys.executable
    if not Path(python).is_absolute():
        raise LaunchdError("cannot pin an absolute interpreter for the wrapper")
    _write_wrapper(root, env_path, Path(wrapper), python)
    local = _plist_path(root)
    local.parent.mkdir(mode=0o700, exist_ok=True)
    payload = plistlib.dumps(job)
    # A loaded job keeps running the old definition until it is booted out.
    # Boot out first so the file on disk and the loaded job cannot disagree.
    _run(["launchctl", "bootout", f"{domain()}/{label}"], check=False)
    local.write_bytes(payload)
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_bytes(payload)
    _run(["launchctl", "bootstrap", domain(), str(installed)])
    return {"status": "installed", "label": label, "plist": str(installed)}


def uninstall(root, *, label=None, dry_run=False):
    """Boot out the job and remove both plist copies. Leaves instance data alone."""
    _require_darwin()
    root = Path(root).absolute()
    label = label or label_for(root.name)
    installed = _installed_path(label)
    local = _plist_path(root)
    if dry_run:
        return {"status": "dry-run", "label": label, "plist": str(installed)}
    _run(["launchctl", "bootout", f"{domain()}/{label}"], check=False)
    for path in (installed, local):
        if path.is_symlink() or path.is_file():
            path.unlink()
    return {"status": "uninstalled", "label": label}


def status(root, *, label=None):
    """Report the loaded state without changing it."""
    _require_darwin()
    root = Path(root).absolute()
    label = label or label_for(root.name)
    result = _run(["launchctl", "list", label], check=False)
    if result.returncode:
        return {"label": label, "loaded": False}
    pid = re.search(r'"PID" = (\d+)', result.stdout)
    return {"label": label, "loaded": True, "pid": int(pid[1]) if pid else None}


def write_profile(root, *, label=None, allow_upgrade=False, dry_run=False):
    """Provision the upgrade profile that gates supervised maintenance.

    Kept separate from install: a runnable agent does not imply that automated
    upgrades are permitted for this instance.
    """
    _require_darwin()
    root = Path(root).absolute()
    label = label or label_for(root.name)
    path = root / "upgrade-profile.json"
    value = {
        "format_version": 1,
        "role": "client",
        "root": str(root),
        "label": label,
        "allow_upgrade": bool(allow_upgrade),
    }
    if dry_run:
        return {"status": "dry-run", "profile": str(path), "value": value}
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return {"status": "written", "profile": str(path), "allow_upgrade": bool(allow_upgrade)}
