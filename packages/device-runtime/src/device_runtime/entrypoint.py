"""Stable entry point text and the release pointer name.

This module is deliberately dependency-free: it is copied into the supervisor
bootstrap runtime, which ships only a handful of modules and has no imported
package graph. Both the installer and the native helper must agree on this text,
so it has to live where either side can import it without dragging in
``installation`` or ``packaging``.
"""

POINTER = "current"

# Deliberately stdlib-only: rebuilding cannot depend on an intact active venv.
RUNNER = """#!/usr/bin/env python3
import fcntl
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import signal
import re

root = Path(__file__).resolve().parent
try:
    # The launcher already resolved the release from the single-line pointer and
    # selected this interpreter. Re-check that the pointer still names the same
    # release under the shared lease: a switch that landed between the launcher's
    # read and this lock must not silently run a release the pointer abandoned.
    if len(sys.argv) != 3:
        raise ValueError("usage: run <release> <center|mcp|client>")
    expected_release = sys.argv[1]
    role = sys.argv[2]
    with (root / ".installation.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        active = json.loads((root / "active.json").read_text())
        config = json.loads((root / "config" / "roles.json").read_text())
        if role not in active["roles"]:
            raise ValueError("role is not installed")
        release = active["release"]
        if not isinstance(release, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+!-]*", release):
            raise ValueError("invalid active release")
        if release != expected_release:
            raise ValueError("active release changed before startup")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        venv = root / "releases" / release / role / "venv"
        if role == "mcp":
            env["DEVICE_RUNTIME_API_URL"] = config[role]["server_url"]
            argv = [str(venv / "bin" / "python"), "-m", "device_runtime.mcp_server"]
        else:
            argv = [str(venv / "bin" / "python"), "-m", "device_runtime.cli",
                    "server" if role == "center" else "client",
                    "--state-dir", str(root / "state" / role)]
            if role == "center":
                argv += ["--host", "127.0.0.1", "--port", str(config[role]["port"])]
            else:
                argv += ["--server-url", config[role]["server_url"],
                         "--device-id", config[role]["device_id"]]
        if role == "client":
            for name in ("max_concurrent_requests", "max_concurrent_queries", "max_active_tasks"):
                if name in config[role]:
                    value = config[role][name]
                    if type(value) is not int or value < 1:
                        raise ValueError("concurrency limits must be positive integers")
                    argv += ["--" + name.replace("_", "-"), str(value)]
        # Install handlers BEFORE spawning: signals received while Popen has not
        # returned are queued until its child handle becomes available.
        process = None
        pending_signals = []
        def forward(signum, frame):
            if process is None:
                pending_signals.append(signum)
            elif process.poll() is None:
                process.send_signal(signum)
        signal.signal(signal.SIGTERM, forward)
        signal.signal(signal.SIGINT, forward)
        process = subprocess.Popen(argv, cwd=root, env=env, pass_fds=(lock.fileno(),))
        for signum in pending_signals:
            forward(signum, None)
        raise SystemExit(process.wait())
except (OSError, ValueError, KeyError, TypeError):
    print("device instance: invalid configuration, unavailable environment or installation busy", file=sys.stderr)
    raise SystemExit(2)
"""

# The stable entry point. It must keep working when a release is broken, so it
# is plain POSIX shell with no dependency on any interpreter. It reads the
# single-line release pointer and execs that release's own interpreter; the
# pointer is the only thing an activation changes.
LAUNCHER = """#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$#" -ne 1 ]; then
 echo 'usage: run <center|mcp|client>' >&2
 exit 2
fi
# Read the pointer without parsing JSON: an activation rewrites this file with
# a single atomic replace, so a torn read cannot expose half a release name.
release=$(cat "$root/current" 2>/dev/null) || {
 echo 'device instance: release pointer is unreadable' >&2
 exit 2
}
# Validate before using it in a path: the pointer is writable only by the
# instance owner, but a malformed value must fail closed, not become a path.
case "$release" in
 ''|*[!A-Za-z0-9_.+!-]*)
  echo 'device instance: release pointer is invalid' >&2
  exit 2
  ;;
esac
# A leading dot or dash would still pass the character check, so reject those
# two shapes explicitly rather than trusting the pattern above.
case "$release" in
 .*|-*) echo 'device instance: release pointer is invalid' >&2; exit 2 ;;
esac
interpreter="$root/releases/$release/runtime/bin/python3.13"
if [ ! -x "$interpreter" ]; then
 echo 'device instance: active release has no executable interpreter' >&2
 exit 2
fi
exec "$interpreter" -I "$root/run-runner" "$release" "$1"
"""

# Same body without a shebang: the launcher names the interpreter explicitly.
RUNNER_BODY = RUNNER.split("\n", 1)[1]
