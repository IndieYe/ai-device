"""Real finite fd-sharing process, no launchctl or actual registration access."""

import os
import subprocess
import sys

import pytest

from device_runtime.client_macos_management import management_lock

CHILD = r"""
import os, sys
from pathlib import Path
from device_runtime import client_macos_start_entry as entry
from device_runtime.client_macos_management import management_lock_matches
fd=int(sys.argv[1]);directory=Path(sys.argv[2]);mode=sys.argv[3]
if fd!=4:
    os.dup2(fd,4);os.close(fd)
entry.MANAGEMENT_DIRECTORY=directory
real_matches=management_lock_matches
owner=os.getuid()
entry.management_lock_matches=lambda fd,path,owner_uid: real_matches(fd,path,owner_uid=owner)
entry.os.getuid=lambda: 0
entry.os.geteuid=lambda: 0
entry.sys.platform='darwin'
def invoke(*,guard_current):
    assert not os.get_inheritable(4)
    assert guard_current()
    if mode=='failure': raise entry.MacStartError('private detail')
    if mode=='cancel':
        import signal, threading
        from device_runtime import client_macos_query as query
        timer=threading.Timer(.1,lambda: os.kill(os.getpid(),signal.SIGTERM))
        timer.start()
        try:
            query._capture([sys.executable,'-I','-c','import time;time.sleep(3)'],timeout=5)
        finally:
            timer.join(timeout=1)
            try: os.waitpid(-1,os.WNOHANG)
            except ChildProcessError: pass
            else: raise AssertionError('query child not reaped')
entry._request_registered_start_held=invoke
if mode=='replacement':
    path=directory/'management.lock'
    path.rename(directory/'retained.lock');path.touch(mode=0o600)
if mode=='closed': os.close(4)
try:
    entry.main(['--inherit-management-lock'] if mode!='arguments' else ['--wrong'])
finally:
    try: os.fstat(4)
    except OSError: pass
    else: raise AssertionError('fd leaked')
"""


@pytest.mark.skipif(sys.platform != "darwin", reason="Mac inherited flock contract")
@pytest.mark.parametrize(
    "mode", ["success", "failure", "arguments", "closed", "replacement", "cancel"]
)
def test_inherited_management_fd_closes_without_unlocking_parent(tmp_path, mode):
    directory = tmp_path / "management"
    args = {"owner_uid": os.getuid(), "owner_gid": os.getgid()}
    with management_lock(directory, **args) as descriptor:
        result = subprocess.run(
            [sys.executable, "-c", CHILD, str(descriptor), str(directory), mode],
            pass_fds=(descriptor,),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == (0 if mode == "success" else 2 if mode == "arguments" else 1), (
            result.stderr
        )
        assert result.stdout == (
            "AI_DEVICE_REGISTERED_START_REQUESTED_V1\n" if mode == "success" else ""
        )
        assert "private detail" not in result.stderr
        # Child close never unlocks shared open-file-description. For replacement
        # test the retained original inode, not the newly created path.
        path = directory / ("retained.lock" if mode == "replacement" else "management.lock")
        import fcntl

        with path.open("r+") as independent, pytest.raises(BlockingIOError):
            fcntl.flock(independent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.fstat(descriptor)  # Parent copy remains open.
    if mode != "replacement":
        with management_lock(directory, **args):
            pass
