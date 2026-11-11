"""Isolated native prototype tests; never install or load a service."""

import fcntl
import platform
import select
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    (platform.system(), platform.machine()) != ("Darwin", "arm64"),
    reason="native arm64 prototype",
)


@pytest.fixture
def launcher(tmp_path):
    output = tmp_path / "launcher"
    source = Path(__file__).parents[1] / "native/client-lock-exec-prototype.c"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-arch",
            "arm64",
            "-mmacosx-version-min=11.0",
            str(source),
            "-o",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


@pytest.fixture
def lock(tmp_path):
    path = tmp_path / "run.lock"
    path.touch(mode=0o600)
    return path


def test_busy_never_executes_target(launcher, lock, tmp_path):
    marker = tmp_path / "executed"
    with lock.open("r+b") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [
                str(launcher),
                str(lock),
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
                str(marker),
            ],
            check=False,
            timeout=5,
        )
    assert result.returncode == 75
    assert not marker.exists()


def test_exec_retains_pid_and_lock_until_target_exit(launcher, lock):
    code = """
import fcntl, os, sys
fcntl.fcntl(3, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
print(os.getpid(), flush=True)
sys.stdin.buffer.read(1)
os._exit(7)
"""
    child = subprocess.Popen(
        [str(launcher), str(lock), sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        ready, _, _ = select.select([child.stdout], [], [], 5)
        assert ready and int(child.stdout.readline()) == child.pid
        with lock.open("r+b") as probe, pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        child.stdin.close()
        child.wait(timeout=5)
        child.stdout.close()
    assert child.returncode == 7
    with lock.open("r+b") as probe:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_failed_exec_releases_lock(launcher, lock):
    result = subprocess.run(
        [str(launcher), str(lock), "/nonexistent-device-probe"],
        check=False,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 70
    with lock.open("r+b") as probe:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_native_exec_python_adoption(launcher, lock):
    code = """
import os, sys
from pathlib import Path
from device_runtime.client_macos_lock import adopt_runtime_lock
with adopt_runtime_lock(Path(sys.argv[1]), uid=os.getuid()):
    assert not os.get_inheritable(3)
    print('ADOPTED', flush=True)
    import select
    select.select([0], [], [], 5)
"""
    child = subprocess.Popen(
        [str(launcher), str(lock), sys.executable, "-c", code, str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        import selectors

        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(5)
            assert child.stdout.readline() == b"ADOPTED\n"
        with lock.open("r+b") as probe, pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, errors = child.communicate(input=b"finish", timeout=7)
        assert child.returncode == 0, errors
        with lock.open("r+b") as probe:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        if child.poll() is None:
            child.communicate(input=b"finish", timeout=7)
        child.stdout.close()
        child.stderr.close()


def test_replaced_launcher_still_obeys_existing_runtime_lock(launcher, lock, tmp_path):
    """Atomic pathname replacement is not proof of Installer write semantics."""
    import os
    import shutil

    code = """
import os, select
os.set_inheritable(3, False)
print('LOCKED', flush=True)
select.select([0], [], [], 5)
"""
    child = subprocess.Popen(
        [str(launcher), str(lock), sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.readline() == b"LOCKED\n"
        old_inode = launcher.stat().st_ino
        new = tmp_path / "replacement"
        shutil.copy2(launcher, new)
        os.replace(new, launcher)
        assert launcher.stat().st_ino != old_inode
        marker = tmp_path / "executed"
        args = [
            str(launcher),
            str(lock),
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(marker),
        ]
        refused = subprocess.run(args, check=False, capture_output=True, timeout=5)
        assert refused.returncode == 75
        assert not marker.exists()
        child.communicate(input=b"finish", timeout=7)
        assert child.returncode == 0
        allowed = subprocess.run(args, check=False, capture_output=True, timeout=5)
        assert allowed.returncode == 0
        assert marker.exists()
    finally:
        if child.poll() is None:
            child.communicate(input=b"finish", timeout=7)
        child.stdout.close()
        child.stderr.close()


@pytest.mark.parametrize("busy", [True, False])
def test_replace_while_old_native_waits_before_lock(launcher, lock, tmp_path, busy):
    import os
    import shutil

    paused = tmp_path / "paused"
    source = Path(__file__).parents[1] / "native/client-lock-exec-prototype.c"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-arch",
            "arm64",
            "-DTEST_BEFORE_LOCK",
            str(source),
            "-o",
            str(paused),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    marker = tmp_path / "target-ran"
    child = subprocess.Popen(
        [
            str(paused),
            str(lock),
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(marker),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.readline() == b"BEFORE_LOCK\n"
        inode = paused.stat().st_ino
        replacement = tmp_path / "new-launcher"
        shutil.copy2(launcher, replacement)
        os.replace(replacement, paused)
        assert paused.stat().st_ino != inode
        with lock.open("r+b") as owner:
            if busy:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _, errors = child.communicate(input=b"go", timeout=7)
            assert child.returncode == (75 if busy else 0), errors
        assert marker.exists() is not busy
    finally:
        if child.poll() is None:
            child.communicate(input=b"go", timeout=7)
        child.stdout.close()
        child.stderr.close()


def test_native_exec_managed_entry_handoff(launcher, lock):
    code = """
import os, sys, select
from pathlib import Path
from types import SimpleNamespace
from device_runtime.client_macos_entry import run_managed
from device_runtime.client_macos_lock import run_or_replace_lock, MacLockBusy
path = Path(sys.argv[1])
account = SimpleNamespace(layout=SimpleNamespace(
    uid=os.getuid(), directory=path.parent, config=path.parent/'client.json'))
def invoke(argv):
    assert argv == ['--config', str(account.layout.config)]
    assert not os.get_inheritable(3)
    print('MANAGED', flush=True)
    select.select([0], [], [], 5)
run_managed(account.layout.config, account=account, invoke=invoke, inherited_descriptor=3)
"""
    # Managed entry uses its declared runtime.lock path, not the generic probe
    # fixture's run.lock. Both launcher and consumer must use the same inode.
    managed_lock = lock.with_name("runtime.lock")
    managed_lock.touch(mode=0o600)
    child = subprocess.Popen(
        [str(launcher), str(managed_lock), sys.executable, "-c", code, str(managed_lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.readline() == b"MANAGED\n"
        with managed_lock.open("r+b") as probe, pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, errors = child.communicate(input=b"finish", timeout=7)
        assert child.returncode == 0, errors
        with managed_lock.open("r+b") as probe:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        if child.poll() is None:
            child.communicate(input=b"finish", timeout=7)
        child.stdout.close()
        child.stderr.close()


def test_native_start_gate_precedes_target_and_releases_after_handoff(launcher, lock, tmp_path):
    import os

    from device_runtime.client_macos_start_gate import prevent_start, provision_start_gate

    gated = tmp_path / "gated"
    source = Path(__file__).parents[1] / "native/client-lock-exec-prototype.c"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-arch",
            "arm64",
            "-DWITH_START_GATE",
            str(source),
            "-o",
            str(gated),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    tmp_path.chmod(0o700)
    provision_start_gate(tmp_path, uid=os.getuid(), gid=os.getgid())
    marker = tmp_path / "target-loaded"
    args = [
        str(gated),
        str(lock),
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
        str(marker),
    ]
    with prevent_start(tmp_path, uid=os.getuid()):
        refused = subprocess.run(args, check=False, capture_output=True, timeout=5)
        assert refused.returncode == 75, refused.stderr
        assert not marker.exists()
    code = "import os,select; os.set_inheritable(3,False); print('RUNNING',flush=True); select.select([0],[],[],5)"
    child = subprocess.Popen(
        [str(gated), str(lock), sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.readline() == b"RUNNING\n"
        # Native shared gate is released after exclusive runtime acquisition,
        # so maintenance can prevent successors without waiting for old exit.
        with prevent_start(tmp_path, uid=os.getuid()):
            with lock.open("r+b") as probe, pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            refused = subprocess.run(args, check=False, capture_output=True, timeout=5)
            assert refused.returncode == 75
            assert not marker.exists()
            child.communicate(input=b"finish", timeout=7)
            assert child.returncode == 0
            refused = subprocess.run(args, check=False, capture_output=True, timeout=5)
            assert refused.returncode == 75
        allowed = subprocess.run(args, check=False, capture_output=True, timeout=5)
        assert allowed.returncode == 0, allowed.stderr
        assert marker.exists()
    finally:
        if child.poll() is None:
            child.communicate(input=b"finish", timeout=7)
        child.stdout.close()
        child.stderr.close()
