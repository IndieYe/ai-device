"""Real isolated evidence for management flock scope, not Installer tests.

A caller-held CLOEXEC lock does not protect an independently surviving child.
Keep this regression when designing package orchestration; do not claim an
outer Python context manager spans macOS installd's transaction lifetime.
"""

import os
import subprocess
import sys

from device_runtime.client_macos_management import management_lock


def test_outer_exit_releases_lock_before_finite_child_exits(tmp_path):
    directory = tmp_path / "management"
    script = r"""
import os, subprocess, sys
from pathlib import Path
from device_runtime.client_macos_management import management_lock
root = Path(sys.argv[1])
with management_lock(root, owner_uid=os.getuid(), owner_gid=os.getgid()):
    # Inherit only stdout as a ready/finish observation pipe. The lock is
    # CLOEXEC and close_fds=True; this is intentionally not a service launch.
    child = subprocess.Popen(
        [sys.executable, "-I", "-c",
         "import os,time,sys; from pathlib import Path; "
         "gate=Path(sys.argv[1]); os.write(1,b'READY\\n'); deadline=time.monotonic()+5; "
         "exec('while not gate.exists() and time.monotonic()<deadline: time.sleep(.01)'); "
         "os.write(1,b'DONE\\n')", str(root / 'finish')],
        stdin=subprocess.DEVNULL, stdout=sys.stdout,
        stderr=subprocess.DEVNULL, close_fds=True,
    )
    os._exit(7)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(directory)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # communicate waits for the finite descendant's pipe EOF too; read READY
    # separately so we can inspect the interval after the owner exited.
    import selectors

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(5), "finite child failed to become ready"
            assert process.stdout.readline() == b"READY\n"
        assert process.wait(timeout=5) == 7
        with (
            management_lock(directory, owner_uid=os.getuid(), owner_gid=os.getgid()),
            selectors.DefaultSelector() as selector,
        ):
            selector.register(process.stdout, selectors.EVENT_READ)
            # The caller's lock is already available before child DONE.
            assert not selector.select(0), "child finished before observation"
        (directory / "finish").touch()
        output, errors = process.communicate(timeout=7)
        assert output == b"DONE\n"
        assert errors == b""
    finally:
        # Both test processes have a finite natural lifetime; no service or
        # process-group signals are used. Drain and reap the owned parent.
        if directory.exists():
            (directory / "finish").touch()
        process.communicate(timeout=7)
        process.stdout.close()
        process.stderr.close()


def test_explicit_fd_handoff_retains_lock(tmp_path):
    """Positive control, not evidence that installd inherits caller fds."""
    import fcntl
    import selectors

    import pytest

    from device_runtime.client_macos_management import MacManagementError

    directory = tmp_path / "management"
    with management_lock(directory, owner_uid=os.getuid(), owner_gid=os.getgid()):
        pass
    with (directory / "management.lock").open("r+b") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    "import os,sys,select; os.fstat(int(sys.argv[1])); "
                    "print('READY',flush=True); select.select([0],[],[],5)"
                ),
                str(owner.fileno()),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(owner.fileno(),),
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(5)
                assert process.stdout.readline() == b"READY\n"
            # close rather than LOCK_UN: the inherited open-file description
            # remains locked. Explicit unlock would affect the child too.
            owner.close()
            with (
                pytest.raises(MacManagementError, match="active"),
                management_lock(directory, owner_uid=os.getuid(), owner_gid=os.getgid()),
            ):
                pytest.fail("lock released early")
            _, errors = process.communicate(input=b"finish", timeout=7)
            assert process.returncode == 0
            assert errors == b""
            with management_lock(directory, owner_uid=os.getuid(), owner_gid=os.getgid()):
                pass
        finally:
            if process.poll() is None:
                process.communicate(input=b"finish", timeout=7)
            process.stdout.close()
            process.stderr.close()
