"""Real socket and cancellation regressions for get wait deadlines."""

import asyncio
import sys
import time

import httpx
import pytest
from conftest import ADMIN, eventually, running_client

from device_runtime.executor import LocalExecutor
from device_runtime.http_client import DeviceAPI


async def test_real_socket_get_running_then_completed(live_server, tmp_path):
    url, _, http = live_server
    executor = LocalExecutor(tmp_path / "client", "laptop-one")
    async with running_client(live_server, tmp_path / "client", executor):
        p = {
            "operation_id": "review-original",
            "device_id": "laptop-one",
            "action": "bash.execute",
            "arguments": {
                "mode": "run",
                "cwd": str(tmp_path),
                "timeout": 0.01,
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib,time;pathlib.Path('started').touch();"
                        "exec(\"while not pathlib.Path('release').exists(): time.sleep(.01)\");"
                        "print('review-ok')"
                    ),
                ],
            },
        }
        r = await http.post("/v1/operations?wait_timeout_ms=5000", json=p)
        assert r.status_code == 200

        async def started_command():
            return (tmp_path / "started").exists()

        await eventually(started_command)
        async with DeviceAPI(url.replace("ws://", "http://"), ADMIN) as api:
            started = time.monotonic()
            snapshot = await api.get_execution("laptop-one", "review-original", timeout=0.05)
            assert time.monotonic() - started < 0.5
            assert snapshot is not None
            if snapshot["result"] is None:
                snapshot = await api.get_operation("laptop-one", snapshot["operation_id"])
            assert snapshot["result"]["data"]["state"] == "running"
            (tmp_path / "release").touch()
            final = None

            async def completed():
                nonlocal final
                final = await api.get_execution("laptop-one", "review-original", timeout=0.2)
                return (final.get("result") or {}).get("data", {}).get("state") == "succeeded"

            await eventually(completed)
            assert final["result"]["data"]["exit_code"] == 0
            assert "review-ok" in final["result"]["data"]["output"]


async def test_external_cancellation_is_not_swallowed():
    entered = asyncio.Event()

    async def handler(request):
        entered.set()
        await asyncio.sleep(30)

    async with DeviceAPI(
        "http://127.0.0.1:1", "test-only-token-12345", transport=httpx.MockTransport(handler)
    ) as api:
        task = asyncio.create_task(api.get_execution("test", "original", timeout=0.01))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
