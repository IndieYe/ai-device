import asyncio
import socket
from contextlib import asynccontextmanager, nullcontext

import httpx
import pytest
import uvicorn

from device_runtime.client import DeviceClient
from device_runtime.protocol import CAPABILITIES, MAX_MESSAGE_BYTES
from device_runtime.server import create_app

ADMIN = "test-only-admin-token-not-a-secret"
TOKEN = "test-only-device-token-not-a-secret"


async def eventually(check, timeout=5):
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(0.01)


class FakeExecutor:
    capabilities = CAPABILITIES

    def __init__(self, *, paused=False):
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        if not paused:
            self.release.set()

    async def __call__(self, operation):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return "succeeded", {
            "location": {"namespace": "device", "device_id": operation.device_id},
            "data": {"success": True, "calls": self.calls},
        }

    async def aclose(self):
        self.closed = True


@pytest.fixture
async def live_server(tmp_path):
    app = create_app(tmp_path / "server", admin_token=ADMIN, device_tokens={"laptop-one": TOKEN})
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, ws_max_size=MAX_MESSAGE_BYTES)
    )
    server.capture_signals = nullcontext
    task = asyncio.create_task(server.serve(sockets=[sock]))

    async def ready():
        if task.done():
            task.result()
        return server.started

    await eventually(ready)
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": "Bearer " + ADMIN}
        ) as http:
            yield f"ws://127.0.0.1:{port}", app.state.device_service, http
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


@asynccontextmanager
async def running_client(live_server, state_dir, executor):
    url, service, _ = live_server
    client = DeviceClient(
        server_url=url,
        device_id="laptop-one",
        token=TOKEN,
        state_dir=state_dir,
        executor=executor,
        reconnect_delay=0.01,
    )
    task = asyncio.create_task(client.run())

    async def ready():
        if task.done():
            task.result()
        return "laptop-one" in service.peers

    await eventually(ready)
    try:
        yield client
    finally:
        await client.close()
        await asyncio.wait_for(task, 10)


def ensure_release_runtime(root, release=None, python=None):
    """Create the interpreter the launcher resolves for a release.

    The stable entry is a shell launcher that execs
    releases/<release>/runtime/bin/python3.13. Simulated installs in tests do
    not build a real runtime, so any test that starts ``run`` (or validates the
    layout) must materialise that path first.
    """
    import sys
    from pathlib import Path

    root = Path(root)
    if release is None:
        release = (root / "current").read_text().strip()
    target = root / "releases" / release / "runtime" / "bin" / "python3.13"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        interpreter = python or sys.executable
        target.write_text(f'#!/bin/sh\nexec {interpreter} "$@"\n')
        target.chmod(0o755)
    return target


def write_entry(root, *, release="0.2.0-fixture", python=None):
    """Give a stub instance a complete stable entry for the legacy layout.

    The helper and lifecycle validate the whole runner body, not just the
    shebang, so a stub that only writes a first line is rejected by design.
    """
    import sys
    from pathlib import Path

    from device_runtime.installation import RUNNER_BODY

    root = Path(root)
    interpreter = str(python or Path(sys.executable).resolve())
    runner = root / "run"
    runner.write_text(f"#!{interpreter}\n" + RUNNER_BODY)
    runner.chmod(0o755)
    return runner

    """Create the interpreter the launcher resolves for a release.

    The stable entry is a shell launcher that execs
    releases/<release>/runtime/bin/python3.13. Simulated installs in tests do
    not build a real runtime, so any test that starts ``run`` (or validates the
    layout) must materialise that path first.
    """
    import sys
    from pathlib import Path

    root = Path(root)
    if release is None:
        release = (root / "current").read_text().strip()
    target = root / "releases" / release / "runtime" / "bin" / "python3.13"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        interpreter = python or sys.executable
        target.write_text(f'#!/bin/sh\nexec {interpreter} "$@"\n')
        target.chmod(0o755)
    return target
