"""Fixed launcher tests: temporary files and linked test doubles, no services."""

import os
import platform
import subprocess
from pathlib import Path

import pytest

from device_runtime.client_macos_lock import provision_runtime_lock, run_or_replace_lock
from device_runtime.client_macos_start_gate import prevent_start, provision_start_gate

pytestmark = pytest.mark.skipif(
    (platform.system(), platform.machine()) != ("Darwin", "arm64"),
    reason="macOS arm64 native launcher",
)
SOURCE = Path(__file__).parents[1] / "native/client-macos-launcher.c"


@pytest.fixture
def launcher(tmp_path):
    # Only this separate test translation unit can substitute passwd/exec.
    # Shipping source has no environment-based home/target override.
    shim = tmp_path / "shim.c"
    shim.write_text(r"""
#include <pwd.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/file.h>
#include <stdio.h>
struct passwd *test_getpwuid(uid_t uid) {
    static struct passwd p;
    p.pw_uid = uid;
    p.pw_dir = getenv("TEST_HOME");
    return &p;
}
int test_execv(const char *path, char *const args[]) {
    const char *expected[] = {
        "/opt/ai-device-client/runtime/bin/python3", "-I", "-B", "-m",
        "device_runtime.client_macos_entry", "--inherit-runtime-lock", "--config"
    };
    if (strcmp(path, expected[0])) _exit(81);
    for (int i=0; i<7; i++)
        if (!args[i] || strcmp(args[i], expected[i])) _exit(82);
    char config[4096], lock[4096], gate[4096];
    snprintf(config, sizeof(config), "%s/Library/Application Support/ai-device-client/client.json", getenv("TEST_HOME"));
    snprintf(lock, sizeof(lock), "%s/Library/Application Support/ai-device-client/runtime.lock", getenv("TEST_HOME"));
    snprintf(gate, sizeof(gate), "%s/Library/Application Support/ai-device-client/start.lock", getenv("TEST_HOME"));
    if (!args[7] || strcmp(args[7], config) || args[8]) _exit(83);
    if (fcntl(3, F_GETFD) != 0) _exit(84);
    int fd = open(lock, O_RDWR);
    if (fd < 0 || flock(fd, LOCK_EX | LOCK_NB) == 0) _exit(85);
    close(fd);
    fd = open(gate, O_RDWR);
    if (fd < 0 || flock(fd, LOCK_EX | LOCK_NB) != 0) _exit(86);
    close(fd);
    if (getenv("TEST_MODULE_PYTHON")) {
        extern char **environ;
        execve(getenv("TEST_MODULE_PYTHON"), args, environ);
        _exit(89);
    }
    if (getenv("TEST_PACKAGED_PYTHON")) {
        extern char **environ;
        char *python_args[] = {getenv("TEST_PACKAGED_PYTHON"), "-I", "-B", "-c", getenv("TEST_PYTHON_BODY"), NULL};
        execve(python_args[0], python_args, environ);
        _exit(88);
    }
    if (getenv("TEST_EXEC_PROBE")) {
        extern char **environ;
        execve(getenv("TEST_EXEC_PROBE"), args, environ);
        _exit(87);
    }
    _exit(42);
}
""")
    output = tmp_path / "launcher"
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
            "-Dgetpwuid=test_getpwuid",
            "-Dexecv=test_execv",
            str(SOURCE),
            str(shim),
            "-o",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "home"
    directory = path / "Library/Application Support/ai-device-client"
    directory.mkdir(parents=True, mode=0o700)
    provision_runtime_lock(directory / "runtime.lock", uid=os.getuid(), gid=os.getgid())
    provision_start_gate(directory, uid=os.getuid(), gid=os.getgid())
    return path, directory


def invoke(launcher, home, *args):
    return subprocess.run(
        [str(launcher), *args],
        check=False,
        env={**os.environ, "TEST_HOME": str(home), "HOME": "/wrong-home"},
        capture_output=True,
        timeout=5,
    )


def test_fixed_target_lock_handoff_and_account_home(launcher, home):
    path, directory = home
    assert invoke(launcher, path).returncode == 42
    # Process exit releases the inherited runtime description.
    with run_or_replace_lock(directory / "runtime.lock", uid=os.getuid()):
        pass


@pytest.mark.parametrize("lock", ["start", "runtime"])
def test_busy_is_success_without_loading_python(launcher, home, lock):
    path, directory = home
    guard = (
        prevent_start(directory, uid=os.getuid())
        if lock == "start"
        else run_or_replace_lock(directory / "runtime.lock", uid=os.getuid())
    )
    with guard:
        assert invoke(launcher, path).returncode == 0


@pytest.mark.parametrize("name", ["start.lock", "runtime.lock"])
@pytest.mark.parametrize("kind", ["missing", "public", "symlink", "fifo", "hardlink"])
def test_invalid_lock_refuses_exec_and_never_repairs(launcher, home, name, kind):
    path, directory = home
    lock = directory / name
    lock.unlink()
    if kind == "public":
        lock.touch(mode=0o644)
    elif kind == "symlink":
        lock.symlink_to(directory / "other")
    elif kind == "fifo":
        os.mkfifo(lock, 0o600)
    elif kind == "hardlink":
        other = directory / "other"
        other.touch(mode=0o600)
        os.link(other, lock)
    before = lock.lstat() if kind != "missing" else None
    assert invoke(launcher, path).returncode == 70
    if before is None:
        assert not lock.exists()
    else:
        after = lock.lstat()
        assert (before.st_ino, before.st_mode) == (after.st_ino, after.st_mode)


def test_arguments_and_public_directory_refused(launcher, home):
    path, directory = home
    assert invoke(launcher, path, "/bin/true").returncode == 70
    directory.chmod(0o755)
    assert invoke(launcher, path).returncode == 70


def test_published_launcher_real_exec_preserves_runtime_fd(launcher, home, tmp_path):
    import shutil

    path, directory = home
    probe_source = tmp_path / "exec-probe.c"
    probe_source.write_text(r"""
#include <unistd.h>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc,char **argv) {
    if(argc!=8 || strcmp(argv[0],"/opt/ai-device-client/runtime/bin/python3") ||
       strcmp(argv[4],"device_runtime.client_macos_entry"))return 91;
    if(fcntl(3,F_GETFD)!=0)return 92;
    struct stat held,current;
    char lock[4096],gate[4096];
    snprintf(lock,sizeof(lock),"%s/Library/Application Support/ai-device-client/runtime.lock",getenv("TEST_HOME"));
    snprintf(gate,sizeof(gate),"%s/Library/Application Support/ai-device-client/start.lock",getenv("TEST_HOME"));
    if(fstat(3,&held) || stat(lock,&current) || held.st_ino!=current.st_ino || held.st_dev!=current.st_dev)return 93;
    int fd=open(lock,O_RDWR);if(fd<0 || flock(fd,LOCK_EX|LOCK_NB)==0)return 94;close(fd);
    fd=open(gate,O_RDWR);if(fd<0 || flock(fd,LOCK_EX|LOCK_NB))return 95;close(fd);
    puts("EXEC_RUNTIME_HELD_GATE_RELEASED");return 0;
}
""")
    program = tmp_path / "program"
    (program / "bin").mkdir(parents=True)
    (program / "runtime/bin").mkdir(parents=True)
    published = program / "bin/ai-device-client-launcher"
    shutil.copy2(launcher, published)
    probe = program / "runtime/bin/python3"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(probe_source),
            "-o",
            str(probe),
        ],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [str(published)],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        env={
            **os.environ,
            "TEST_HOME": str(path),
            "HOME": "/wrong-home",
            "TEST_EXEC_PROBE": str(probe),
        },
    )
    assert result.stdout == "EXEC_RUNTIME_HELD_GATE_RELEASED\n"
    with run_or_replace_lock(directory / "runtime.lock", uid=os.getuid()):
        pass


@pytest.mark.skipif(
    not os.environ.get("AI_DEVICE_TEST_MAC_TREE"), reason="explicit packaged tree required"
)
@pytest.mark.parametrize("failure", [False, True])
def test_launcher_packaged_python_managed_cleanup(launcher, home, tmp_path, failure):
    import shutil

    path, directory = home
    tree = tmp_path / "program"
    shutil.copytree(
        Path(os.environ["AI_DEVICE_TEST_MAC_TREE"]),
        tree,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    # Overlay current entry/lock modules only in this test tree.
    package = tree / "runtime/lib/python3.13/site-packages/device_runtime"
    for name in ("client_macos_entry.py", "client_macos_lock.py"):
        shutil.copyfile(Path(__file__).parents[1] / "src/device_runtime" / name, package / name)
    published = tree / "bin/ai-device-client-launcher"
    shutil.copy2(launcher, published)
    body = r"""
import asyncio, fcntl, os
from pathlib import Path
from types import SimpleNamespace
from device_runtime.client_macos_entry import run_managed
root=Path(os.environ["TEST_HOME"])/"Library/Application Support/ai-device-client"
assert os.get_inheritable(3)
def locked():
    with (root/"runtime.lock").open("r+") as stream:
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: pass
        else: raise AssertionError("runtime exclusion lost")
    with (root/"start.lock").open("r+") as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
    assert not os.get_inheritable(3)
events=[]
async def child():
    try:
        events.append("running");locked()
        await asyncio.Event().wait()
    finally:
        locked();events.append("async-cleanup")
async def main():
    asyncio.create_task(child())
    await asyncio.sleep(0)
    if os.environ["TEST_FAILURE"]=="1": raise RuntimeError("fixture failure")
def invoke(args):
    assert args==["--config",str(root/"client.json")]
    locked()
    asyncio.run(main())
account=SimpleNamespace(layout=SimpleNamespace(directory=root,config=root/"client.json",uid=os.getuid()))
try:
    run_managed(root/"client.json",account=account,invoke=invoke,inherited_descriptor=3)
except RuntimeError:
    assert os.environ["TEST_FAILURE"]=="1"
assert events==["running","async-cleanup"]
try: os.fstat(3)
except OSError: pass
else: raise AssertionError("fd not consumed")
print("PACKAGED_PYTHON_CLEANUP_LOCKED")
"""
    result = subprocess.run(
        [str(published)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "TEST_HOME": str(path),
            "HOME": "/wrong-home",
            "TEST_PACKAGED_PYTHON": str(tree / "runtime/bin/python3"),
            "TEST_PYTHON_BODY": body,
            "TEST_FAILURE": str(int(failure)),
        },
    )
    assert result.stdout == "PACKAGED_PYTHON_CLEANUP_LOCKED\n"
    with run_or_replace_lock(directory / "runtime.lock", uid=os.getuid()):
        pass


@pytest.mark.skipif(
    not os.environ.get("AI_DEVICE_TEST_MAC_TREE"), reason="explicit packaged tree required"
)
@pytest.mark.parametrize("failure", [False, True])
def test_launcher_actual_module_entry(launcher, home, tmp_path, failure):
    import json
    import shutil

    path, directory = home
    tree = tmp_path / "program"
    shutil.copytree(
        Path(os.environ["AI_DEVICE_TEST_MAC_TREE"]),
        tree,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    package = tree / "runtime/lib/python3.13/site-packages/device_runtime"
    for name in ("client_macos_entry.py", "client_macos_lock.py"):
        shutil.copyfile(Path(__file__).parents[1] / "src/device_runtime" / name, package / name)
    (package / "__init__.py").write_text(r"""
import asyncio, atexit, fcntl, json, os, sys, types
from pathlib import Path
from . import client_macos_account
root=Path(os.environ["TEST_HOME"])/"Library/Application Support/ai-device-client"
assert os.get_inheritable(3)
events=[]
def locked():
    assert not os.get_inheritable(3)
    with (root/"runtime.lock").open("r+") as stream:
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: pass
        else: raise AssertionError("runtime exclusion lost")
async def worker():
    try:
        locked();events.append("running")
        await asyncio.Event().wait()
    finally:
        locked();events.append("cleanup")
async def run():
    asyncio.create_task(worker());await asyncio.sleep(0)
    if os.environ["TEST_FAILURE"]=="1": raise client_macos_account.MacAccountError("fixture")
def main(argv, *, lease_exit=False):
    assert lease_exit is True
    assert argv==["--config",str(root/"client.json")]
    locked();asyncio.run(run())
client=types.ModuleType("device_runtime.client_cli");client.main=main
sys.modules[client.__name__]=client
account=types.SimpleNamespace(layout=types.SimpleNamespace(directory=root,config=root/"client.json",uid=os.getuid()))
client_macos_account.resolve_account=lambda name: account
def finished():
    try: os.fstat(3)
    except OSError: events.append("fd-closed")
    else: events.append("fd-leaked")
    (root/"module-result.json").write_text(json.dumps(events))
atexit.register(finished)
""")
    published = tree / "bin/ai-device-client-launcher"
    shutil.copy2(launcher, published)
    result = subprocess.run(
        [str(published)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "TEST_HOME": str(path),
            "HOME": "/wrong-home",
            "TEST_MODULE_PYTHON": str(tree / "runtime/bin/python3"),
            "TEST_FAILURE": str(int(failure)),
        },
    )
    assert result.returncode == (2 if failure else 0), result.stderr
    assert json.loads((directory / "module-result.json").read_text()) == [
        "running",
        "cleanup",
        "fd-closed",
    ]
    with run_or_replace_lock(directory / "runtime.lock", uid=os.getuid()):
        pass


@pytest.mark.skipif(
    not os.environ.get("AI_DEVICE_TEST_MAC_TREE"), reason="explicit packaged tree required"
)
@pytest.mark.parametrize("failure", [False, True])
def test_launcher_real_client_cli_shutdown(launcher, home, tmp_path, failure):
    """Real CLI/DeviceClient run-close; only external adapters are replaced."""
    import json
    import shutil

    path, directory = home
    tree = tmp_path / "program"
    shutil.copytree(
        Path(os.environ["AI_DEVICE_TEST_MAC_TREE"]),
        tree,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    package = tree / "runtime/lib/python3.13/site-packages/device_runtime"
    shutil.copytree(
        Path(__file__).parents[1] / "src/device_runtime",
        package,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    initializer = (package / "__init__.py").read_text()
    (package / "__init__.py").write_text(
        initializer
        + r"""
import asyncio, atexit, fcntl, json, os, signal
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from . import client_macos_account, client_config, executor, client, maintenance
root=Path(os.environ["TEST_HOME"])/"Library/Application Support/ai-device-client"
events=[]
runtime=None
def locked():
    assert not os.get_inheritable(3)
    with (root/"runtime.lock").open("r+") as stream:
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: pass
        else: raise AssertionError("runtime exclusion lost")
account=SimpleNamespace(layout=SimpleNamespace(directory=root,config=root/"client.json",uid=os.getuid()))
client_macos_account.resolve_account=lambda name: account
def load(path):
    assert path==root/"client.json"
    locked();events.append("config")
    return SimpleNamespace(server_url="wss://isolated.invalid",device_id="isolated",
        state_dir=root,maintenance=True,max_active_tasks=1,
        max_concurrent_requests=1,max_concurrent_queries=1), "test-only-token-0000000000"
client_config.load=load
class Executor:
    capabilities=[]
    def __init__(self, state_dir, device_id, *, max_active_tasks):
        assert (state_dir,device_id,max_active_tasks)==(root,"isolated",1)
        self.tasks=self
    async def active_count(self): return 0
    async def aclose(self):
        locked();await asyncio.sleep(.01);locked();events.append("executor_closed")
        if os.environ["TEST_FAILURE"]=="1": raise RuntimeError("fixture close failure")
executor.LocalExecutor=Executor
class Journal:
    def __init__(self, *args, **kwargs): pass
    async def initialize(self): locked();events.append("initialized")
    async def unconfirmed(self, after): return []
    async def disconnect(self, device_id): locked();events.append("disconnected")
    async def close(self): locked();events.append("journal_closed")
client.Journal=Journal
class Maintenance:
    def __init__(self, value, path):
        global runtime
        runtime=value
    async def start(self): locked()
    async def close(self): locked();events.append("maintenance_closed")
maintenance.MaintenanceServer=Maintenance
class Socket:
    async def send(self, message): locked()
    async def recv(self):
        return json.dumps({"v":1,"type":"welcome","device_id":"isolated"})
    def __aiter__(self): return self
    async def __anext__(self):
        locked();events.append("running")
        assert runtime._maintenance_exit_callback is not None
        assert (await runtime.maintenance_acquire("fixture"))["acquired"] is True
        await runtime.maintenance_exit("fixture")
        assert runtime._maintenance_exit_committed
        await asyncio.Event().wait()
    async def close(self, **kwargs): locked();events.append("socket_closed")
@asynccontextmanager
async def connect(*args, **kwargs):
    assert args[0].startswith("wss://isolated.invalid")
    try: yield Socket()
    finally: locked();events.append("connection_closed")
client.connect=connect
def finished():
    assert runtime._closed.is_set()
    assert runtime._close_task.done()
    assert runtime._maintenance_exit_callback is None
    assert signal.getsignal(signal.SIGTERM)==signal.SIG_DFL
    try: os.fstat(3)
    except OSError: events.append("fd-closed")
    else: events.append("fd-leaked")
    (root/"cli-result.json").write_text(json.dumps(events))
atexit.register(finished)
"""
    )
    published = tree / "bin/ai-device-client-launcher"
    shutil.copy2(launcher, published)
    result = subprocess.run(
        [str(published)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "TEST_HOME": str(path),
            "HOME": "/wrong-home",
            "TEST_MODULE_PYTHON": str(tree / "runtime/bin/python3"),
            "TEST_FAILURE": str(int(failure)),
        },
    )
    assert result.returncode == (2 if failure else 0), result.stderr
    assert (directory / "cli-result.json").exists(), result.stderr
    assert json.loads((directory / "cli-result.json").read_text()) == [
        "config",
        "initialized",
        "running",
        "connection_closed",
        "maintenance_closed",
        "executor_closed",
        "disconnected",
        "journal_closed",
        "fd-closed",
    ]
    with run_or_replace_lock(directory / "runtime.lock", uid=os.getuid()):
        pass


def test_successor_launchers_blocked_after_runtime_release_until_gate_handoff(launcher, home):
    from concurrent.futures import ThreadPoolExecutor

    path, directory = home
    gate_inode = (directory / "start.lock").stat().st_ino
    runtime_inode = (directory / "runtime.lock").stat().st_ino
    with prevent_start(directory, uid=os.getuid()):
        # Model the old instance releasing runtime while management retains
        # the startup barrier; these are finite processes, not launchd jobs.
        with run_or_replace_lock(directory / "runtime.lock", uid=os.getuid()):
            pass
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: invoke(launcher, path), range(8)))
        assert all(result.returncode == 0 for result in results)
        assert all(not result.stdout and not result.stderr for result in results)
        # Exit 42 is the linked exec seam. Busy starters must not reach it.
        with run_or_replace_lock(directory / "runtime.lock", uid=os.getuid()):
            pass
    assert (directory / "start.lock").stat().st_ino == gate_inode
    assert (directory / "runtime.lock").stat().st_ino == runtime_inode
    assert invoke(launcher, path).returncode == 42
