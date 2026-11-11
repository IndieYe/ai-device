"""Cross-process maintenance protocol, not a running client/launchd test."""

import json
import os
import select
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from device_runtime.client_macos_lease import bind_lease, request_original_exit
from device_runtime.client_macos_processes import inspect_identity, wait_original_exit
from device_runtime.maintenance import ClientBusy, idle_lease

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin kernel peer PID")

CHILD = r"""
import asyncio, json, os, sys
from pathlib import Path
from types import SimpleNamespace
from device_runtime.client import DeviceClient
from device_runtime.maintenance import MaintenanceServer

async def main():
    root, busy = Path(sys.argv[1]), sys.argv[2] == 'busy'
    async def count():
        return 1 if busy else 0
    client = DeviceClient(server_url='ws://localhost:9001', device_id='isolated',
        token='test-only-token-0000000000000000', state_dir=root,
        executor=SimpleNamespace(tasks=SimpleNamespace(active_count=count)))
    client._initialized = True
    committed = asyncio.Event()
    def commit():
        committed.set()
        if sys.argv[2] == 'lost_ack':
            for writer in tuple(server.connections):
                writer.close()
    client._maintenance_exit_callback = commit
    server = MaintenanceServer(client, root/'control.sock')
    await server.start()
    print(json.dumps({'pid': os.getpid()}), flush=True)
    try:
        # Finite even when parent fails; no signal-based cleanup required.
        deadline = asyncio.get_running_loop().time() + 8
        while not committed.is_set() and not (root/'finish').exists():
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError('finite fixture deadline')
            await asyncio.sleep(.01)
        if committed.is_set():
            # Yield so acknowledgement can flush; real acknowledgement loss
            # is covered separately and remains unknown, never replayed.
            await asyncio.sleep(.05)
        (root/'result.json').write_text(json.dumps({
            'committed': client._maintenance_exit_committed,
            'closed': client._closed.is_set(),
            'admission_open': client._maintenance_id is None}))
    finally:
        await server.close()
asyncio.run(main())
"""


@pytest.mark.parametrize("mode", ["idle", "busy", "abandon", "lost_ack"])
def test_cross_process_peer_exit_and_refusal(mode):
    # Keep AF_UNIX path under Darwin's length limit regardless of pytest root.
    with tempfile.TemporaryDirectory(prefix="device-peer-") as directory:
        root = Path(directory)
        child = subprocess.Popen(
            [sys.executable, "-c", CHILD, directory, mode],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        reaper = threading.Thread(target=child.wait)
        reaper.start()
        try:
            assert select.select([child.stdout], [], [], 5)[0], "fixture not ready"
            ready = json.loads(child.stdout.readline())
            assert ready["pid"] == child.pid != os.getpid()
            original = inspect_identity(child.pid)
            assert original is not None
            if mode == "idle":
                with idle_lease(root / "control.sock") as lease:
                    assert request_original_exit(lease, timeout=5) == original
                assert inspect_identity(original.pid) is None
            elif mode == "lost_ack":
                with idle_lease(root / "control.sock") as lease:
                    with pytest.raises(ValueError, match="unknown"):
                        request_original_exit(lease, timeout=5)
                    with pytest.raises(ValueError, match="already attempted"):
                        lease.request_exit()
                wait_original_exit(original, timeout=5)
            elif mode == "busy":
                with pytest.raises(ClientBusy), idle_lease(root / "control.sock"):
                    pytest.fail("busy fixture acquired lease")
                assert inspect_identity(child.pid) == original
                (root / "finish").touch()
            else:
                with idle_lease(root / "control.sock") as lease:
                    assert bind_lease(lease) == original
                # A second acquire proves EOF restored admission. No exit was
                # requested on either lease, and the process must remain alive.
                for _ in range(50):
                    try:
                        with idle_lease(root / "control.sock"):
                            break
                    except ValueError:
                        import time

                        time.sleep(0.01)
                else:
                    pytest.fail("abandoned lease did not resume")
                assert inspect_identity(child.pid) == original
                (root / "finish").touch()
            reaper.join(timeout=6)
            assert not reaper.is_alive()
            assert child.returncode == 0, child.stderr.read().decode()
            result = json.loads((root / "result.json").read_text())
            assert result["committed"] is (mode in {"idle", "lost_ack"})
            assert result["closed"] is (mode in {"idle", "lost_ack"})
            if mode == "busy":
                assert result["admission_open"]
        finally:
            (root / "finish").touch()
            reaper.join(timeout=10)
            child.wait(timeout=2)
            child.stdout.close()
            child.stderr.close()
        assert not reaper.is_alive()


RUNTIME_CHILD = r"""
import asyncio, json, os, sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from device_runtime import client as module
from device_runtime.client import DeviceClient
from device_runtime.client_macos_lock import run_or_replace_lock

root = Path(sys.argv[1])
async def main():
    ready = asyncio.Event()
    events = []
    class Executor:
        capabilities = []
        tasks = None
        async def active_count(self):
            return 0
        async def aclose(self):
            # Real close must finish this step before process/lock disappearance.
            await asyncio.sleep(.05)
            events.append('executor_closed')
    executor = Executor()
    executor.tasks = executor
    runtime = DeviceClient(server_url='ws://localhost:9001', device_id='isolated',
        token='test-only-token-0000000000000000', state_dir=root,
        executor=executor, maintenance=True)
    class Socket:
        async def send(self, message):
            pass
        async def recv(self):
            return json.dumps({'v':1,'type':'welcome','device_id':'isolated'})
        def __aiter__(self):
            return self
        async def __anext__(self):
            ready.set()
            await asyncio.Event().wait()
        async def close(self, **kwargs):
            events.append('socket_closed')
    @asynccontextmanager
    async def connect(*args, **kwargs):
        socket = Socket()
        try:
            yield socket
        finally:
            await socket.close()
    async def noop(*args):
        pass
    async def journal_close():
        events.append('journal_closed')
    module.connect = connect
    runtime.journal.initialize = noop
    runtime.journal.disconnect = noop
    runtime.journal.close = journal_close
    runtime._reconcile = noop
    task = asyncio.create_task(runtime.run())
    loop = asyncio.get_running_loop()
    runtime._maintenance_exit_callback = lambda: loop.call_soon(task.cancel)
    try:
        await asyncio.wait_for(ready.wait(), 5)
        print(json.dumps({'pid':os.getpid()}), flush=True)
        try:
            # Finite fixture deadline is not a production stop timeout.
            await asyncio.wait_for(asyncio.shield(task), 8)
        except asyncio.CancelledError:
            assert runtime._maintenance_exit_committed
        assert events == ['socket_closed','executor_closed','journal_closed'], events
        (root/'runtime-result.json').write_text(json.dumps(events))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
with run_or_replace_lock(root/'runtime.lock', uid=os.getuid()):
    asyncio.run(main())
"""


@pytest.mark.parametrize("with_gate", [False, True])
def test_cross_process_runtime_exit_holds_lock_through_cleanup(with_gate):
    from contextlib import ExitStack

    from device_runtime.client_macos_lock import MacLockBusy, run_or_replace_lock
    from device_runtime.client_macos_processes import wait_original_exit
    from device_runtime.client_macos_start_gate import (
        prevent_start,
        provision_start_gate,
        startup_handoff,
    )

    with tempfile.TemporaryDirectory(prefix="device-runtime-peer-") as directory:
        root = Path(directory)
        root.chmod(0o700)
        lock = root / "runtime.lock"
        lock.touch(mode=0o600)
        provision_start_gate(root, uid=os.getuid(), gid=os.getgid())
        child = subprocess.Popen(
            [sys.executable, "-c", RUNTIME_CHILD, directory],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        reaper = threading.Thread(target=child.wait)
        reaper.start()
        guard = ExitStack()
        try:
            assert select.select([child.stdout], [], [], 5)[0]
            assert json.loads(child.stdout.readline())["pid"] == child.pid
            if with_gate:
                guard.enter_context(prevent_start(root, uid=os.getuid()))
                with pytest.raises(MacLockBusy), startup_handoff(root, uid=os.getuid()):
                    pytest.fail("successor passed closed gate")
            with pytest.raises(MacLockBusy), run_or_replace_lock(lock, uid=os.getuid()):
                pytest.fail("runtime lock missing")
            original = inspect_identity(child.pid)
            with idle_lease(root / "maintenance.sock") as lease:
                assert bind_lease(lease) == original
                # Real cancellation may close the socket before ACK flush.
                # Either ACK or unknown is legitimate; neither permits replay.
                try:
                    lease.request_exit()
                except ValueError as exc:
                    assert "unknown" in str(exc)
                with pytest.raises(ValueError, match="already attempted"):
                    lease.request_exit()
            wait_original_exit(original, timeout=5)
            reaper.join(timeout=2)
            assert child.returncode == 0, child.stderr.read().decode()
            assert json.loads((root / "runtime-result.json").read_text()) == [
                "socket_closed",
                "executor_closed",
                "journal_closed",
            ]
            with run_or_replace_lock(lock, uid=os.getuid()):
                if with_gate:
                    with pytest.raises(MacLockBusy), startup_handoff(root, uid=os.getuid()):
                        pytest.fail("successor entered after original exit")
        finally:
            guard.close()
            # Fixture's own deadline guarantees natural cleanup, no signal.
            reaper.join(timeout=12)
            child.wait(timeout=2)
            child.stdout.close()
            child.stderr.close()
        assert not reaper.is_alive()
