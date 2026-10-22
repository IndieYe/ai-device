"""Direct regressions for the four execution review findings."""

import asyncio
import sys
import time
from types import SimpleNamespace

import pytest

from device_runtime.executor import LocalExecutor
from device_runtime.http_client import DeviceAPI, DeviceAPIError
from device_runtime.protocol import Operation


def execution(tmp_path, name="race", timeout=1):
    return Operation(
        device_id="test",
        operation_id=name,
        action="bash.execute",
        arguments={
            "mode": "run",
            "cwd": str(tmp_path),
            "timeout": timeout,
            "argv": [sys.executable, "-c", "import time;time.sleep(.1);print('once')"],
        },
    )


async def test_accept_registration_is_atomic_with_query(tmp_path, monkeypatch):
    executor = LocalExecutor(tmp_path / "state", "test")
    accepted, release = asyncio.Event(), asyncio.Event()
    original = executor.tasks.journal.accept

    async def delayed_accept(op, state):
        record = await original(op, state)
        accepted.set()
        await release.wait()
        return record

    monkeypatch.setattr(executor.tasks.journal, "accept", delayed_accept)
    running = asyncio.create_task(executor(execution(tmp_path)))
    query = None
    try:
        await asyncio.wait_for(accepted.wait(), 2)
        query = asyncio.create_task(
            executor(
                Operation(
                    device_id="test",
                    operation_id="query",
                    action="bash.get",
                    arguments={"execution_id": "race"},
                )
            )
        )
        await asyncio.sleep(0.02)
        release.set()
        _, result = await query
        assert result["data"]["state"] != "unknown"
        await running
    finally:
        release.set()
        await asyncio.gather(running, *([query] if query else []), return_exceptions=True)
        await executor.aclose()


async def test_get_zero_wait_and_positive_deadline(monkeypatch):
    api = DeviceAPI("http://127.0.0.1:1", "isolated-test-token")
    waits, ids = [], []

    async def submit(op, *, wait_timeout_ms):
        waits.append(wait_timeout_ms)
        ids.append(op.operation_id)
        if wait_timeout_ms:
            await asyncio.sleep(0.2)
        return {"state": "running", "operation_id": op.operation_id, "result": None}

    monkeypatch.setattr(api, "submit", submit)
    try:
        await api.get_execution("test", "original", timeout=0)
        assert waits == [0]
        started = time.monotonic()
        result = await api.get_execution("test", "original", timeout=0.03)
        assert result["operation_id"] == ids[-1]
        assert result["state"] == "running"
        assert time.monotonic() - started < 0.15
        assert waits[-1] == 0
    finally:
        await api._http.aclose()


def test_cli_preserves_uncertain_query_id(monkeypatch, capsys):
    from device_runtime import operations_cli as cli

    async def fail(_):
        exc = DeviceAPIError("operation_result_uncertain", "query-stable")
        exc.execution_id = "original"
        raise exc

    monkeypatch.setattr(cli, "execute", fail)
    assert (
        cli.run_operations(
            SimpleNamespace(device_id="test", operation_id=None, execution_id="original")
        )
        == 6
    )
    import json

    result = json.loads(capsys.readouterr().out)
    assert result["data"]["operation_id"] == "query-stable"
    assert result["data"]["execution_id"] == "original"


async def test_completed_monitors_are_removed(tmp_path):
    executor = LocalExecutor(tmp_path / "state", "test")
    try:
        for i in range(3):
            await executor(execution(tmp_path, name=f"bg-{i}", timeout=0.01))
        await asyncio.sleep(0.4)
        assert not executor.tasks._monitors
        assert not executor.tasks._owned
    finally:
        await executor.aclose()


async def test_monitor_failure_is_consumed_and_shutdown_cleans(tmp_path, monkeypatch):
    executor = LocalExecutor(tmp_path / "state", "test")
    loop = asyncio.get_running_loop()
    errors = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _, context: errors.append(context))

    async def fail(*_, **__):
        raise OSError("isolated monitor failure")

    monkeypatch.setattr(executor.runtime, "get_background_result", fail)
    try:
        await executor(execution(tmp_path, name="monitor-fail", timeout=0.01))
        for _ in range(50):
            if not executor.tasks._monitors:
                break
            await asyncio.sleep(0.01)
        assert not executor.tasks._monitors
        record = await executor.tasks.journal.get("monitor-fail")
        assert record["state"] == "unknown"
        # A failed monitor cannot prove its process stopped; keep capacity reserved.
        assert "monitor-fail" in executor.tasks._owned
    finally:
        await executor.aclose()
        loop.set_exception_handler(previous)
    assert not executor.tasks._owned
    assert not errors


async def test_uncertain_http_submission_retains_both_ids():
    import httpx

    async def response(request):
        return httpx.Response(503)

    api = DeviceAPI(
        "http://127.0.0.1:1", "isolated-test-token", transport=httpx.MockTransport(response)
    )
    try:
        with pytest.raises(DeviceAPIError) as caught:
            await api.get_execution("test", "original", timeout=0)
        assert caught.value.operation_id.startswith("query-")
        assert caught.value.execution_id == "original"
        assert caught.value.code == "operation_result_uncertain"
    finally:
        await api._http.aclose()


async def test_query_wait_budget_is_sent_to_http_transport():
    import json

    import httpx

    queries = []

    async def response(request):
        queries.append(request.url.params.get("wait_timeout_ms", "0"))
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "device_id": "test",
                "operation_id": payload["operation_id"],
                "state": "unknown",
                "result": None,
            },
        )

    api = DeviceAPI(
        "http://127.0.0.1:1", "isolated-test-token", transport=httpx.MockTransport(response)
    )
    try:
        await api.get_execution("test", "original", timeout=0)
        await api.get_execution("test", "original", timeout=0.05)
        assert queries[0] == "0"
        assert 0 <= int(queries[1]) <= 50
    finally:
        await api._http.aclose()


async def test_cancelled_accept_does_not_execute_and_query_recovers(tmp_path, monkeypatch):
    executor = LocalExecutor(tmp_path / "state", "test")
    accepted = asyncio.Event()
    original = executor.tasks.journal.accept
    spawned = False

    async def delayed(op, state):
        result = await original(op, state)
        accepted.set()
        await asyncio.Event().wait()
        return result

    async def unexpected(*_, **__):
        nonlocal spawned
        spawned = True
        raise AssertionError("cancelled acceptance must not spawn")

    monkeypatch.setattr(executor.tasks.journal, "accept", delayed)
    monkeypatch.setattr(executor.tasks, "run_command", unexpected)
    running = asyncio.create_task(executor(execution(tmp_path)))
    try:
        await asyncio.wait_for(accepted.wait(), 2)
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        _, result = await asyncio.wait_for(
            executor(
                Operation(
                    device_id="test",
                    operation_id="q",
                    action="bash.get",
                    arguments={"execution_id": "race"},
                )
            ),
            1,
        )
        assert result["data"]["state"] == "unknown"
        assert not spawned
    finally:
        await executor.aclose()
