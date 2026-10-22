"""Waiting for a command must not discard a known query receipt."""

import asyncio
import json

import httpx
import pytest

from device_runtime.http_client import DeviceAPI, DeviceAPIError


def receipt(request, state="running"):
    op = json.loads(request.content)
    return {
        "device_id": op["device_id"],
        "operation_id": op["operation_id"],
        "state": "succeeded",
        "result": {
            "location": {"namespace": "device", "device_id": op["device_id"]},
            "data": {"state": state, "execution_id": "original"},
        },
    }


async def test_first_snapshot_survives_short_command_wait():
    completed = []

    async def handler(request):
        await asyncio.sleep(0.04)
        completed.append(request)
        return httpx.Response(200, json=receipt(request))

    async with DeviceAPI(
        "http://127.0.0.1:1", "isolated-test-token", transport=httpx.MockTransport(handler)
    ) as api:
        result = await api.get_execution("test", "original", timeout=0.01)
    assert len(completed) == 1
    assert completed[0].url.params.get("wait_timeout_ms", "0") == "0"
    assert result["result"]["data"]["state"] == "running"


async def test_refresh_deadline_returns_last_known_receipt():
    requests = []

    async def handler(request):
        requests.append(request)
        if len(requests) > 1:
            await asyncio.sleep(0.5)
        return httpx.Response(200, json=receipt(request))

    async with DeviceAPI(
        "http://127.0.0.1:1", "isolated-test-token", transport=httpx.MockTransport(handler)
    ) as api:
        result = await api.get_execution("test", "original", timeout=0.25)
    assert len(requests) >= 2
    assert result["operation_id"] == json.loads(requests[0].content)["operation_id"]
    assert result["result"]["data"]["state"] == "running"


async def test_first_snapshot_transport_failure_keeps_recovery_ids():
    ids = []

    async def handler(request):
        ids.append(json.loads(request.content)["operation_id"])
        raise httpx.ReadTimeout("isolated transport failure")

    async with DeviceAPI(
        "http://127.0.0.1:1", "isolated-test-token", transport=httpx.MockTransport(handler)
    ) as api:
        with pytest.raises(DeviceAPIError) as caught:
            await api.get_execution("test", "original", timeout=0.01)
    assert caught.value.code == "operation_result_uncertain"
    assert caught.value.operation_id == ids[0]
    assert caught.value.execution_id == "original"


async def test_terminal_first_snapshot_does_not_refresh():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json=receipt(request, "succeeded"))

    async with DeviceAPI(
        "http://127.0.0.1:1", "isolated-test-token", transport=httpx.MockTransport(handler)
    ) as api:
        result = await api.get_execution("test", "original", timeout=1)
    assert len(requests) == 1
    assert result["result"]["data"]["state"] == "succeeded"


async def test_pending_receipt_is_preserved_when_poll_deadline_expires():
    known_id = None

    async def handler(request):
        nonlocal known_id
        if request.method == "POST":
            known_id = json.loads(request.content)["operation_id"]
            return httpx.Response(
                200,
                json={
                    "device_id": "test",
                    "operation_id": known_id,
                    "state": "sent",
                    "result": None,
                },
            )
        await asyncio.sleep(0.5)
        raise AssertionError("poll must be cancelled at the command deadline")

    async with DeviceAPI(
        "http://127.0.0.1:1", "isolated-test-token", transport=httpx.MockTransport(handler)
    ) as api:
        result = await api.get_execution("test", "original", timeout=0.25)
    assert result["operation_id"] == known_id
    assert result["state"] == "sent"
    assert result["result"] is None


async def test_refresh_transport_failure_is_not_hidden_by_old_snapshot():
    ids = []

    async def handler(request):
        ids.append(json.loads(request.content)["operation_id"])
        if len(ids) > 1:
            raise httpx.ReadTimeout("isolated refresh failure")
        return httpx.Response(200, json=receipt(request))

    async with DeviceAPI(
        "http://127.0.0.1:1", "isolated-test-token", transport=httpx.MockTransport(handler)
    ) as api:
        with pytest.raises(DeviceAPIError) as caught:
            await api.get_execution("test", "original", timeout=1)
    assert len(ids) == 2
    assert caught.value.operation_id == ids[-1]
    assert caught.value.execution_id == "original"
