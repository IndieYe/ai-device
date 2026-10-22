import json

import httpx
import pytest

from device_runtime.http_client import DeviceAPI, DeviceAPIError
from device_runtime.protocol import Operation

TOKEN = "only-for-isolated-adapter-tests"


def op():
    return Operation(
        operation_id="op-one",
        device_id="laptop-one",
        action="fs.write_file",
        arguments={"path": "/tmp/test", "content": "hello"},
    )


def record(state="unknown", device_id="laptop-one"):
    return {
        "operation_id": "op-one",
        "device_id": device_id,
        "state": state,
        "result": None,
        "operation": op().model_dump(),
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:secret@host",
        "https://host?a=secret",
        "https://host/#secret",
        "file:///tmp/test",
    ],
)
def test_unsafe_center_urls(url):
    with pytest.raises(ValueError):
        DeviceAPI(url, TOKEN)


async def test_post_transport_uncertain_never_retries_or_echoes_token():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("sensitive " + TOKEN, request=request)

    async with DeviceAPI(
        "http://localhost:3333/device-runtime", TOKEN, transport=httpx.MockTransport(handle)
    ) as api:
        with pytest.raises(DeviceAPIError) as caught:
            await api.submit(op())
    assert len(calls) == 1
    assert caught.value.code == "operation_result_uncertain"
    assert "op-one" in str(caught.value) and TOKEN not in str(caught.value)
    assert calls[0].url.path == "/device-runtime/v1/operations"
    assert calls[0].headers["authorization"] == "Bearer " + TOKEN


async def test_unknown_read_does_not_submit_and_checks_target():
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json=record())

    async with DeviceAPI(
        "http://localhost:3333", TOKEN, transport=httpx.MockTransport(handle)
    ) as api:
        result = await api.get_operation("laptop-one", "op-one")
        assert result["state"] == "unknown"
        with pytest.raises(DeviceAPIError) as caught:
            await api.get_operation("other-device", "op-one")
    assert caught.value.code == "device_target_mismatch"
    assert calls == ["GET", "GET"]


@pytest.mark.parametrize("status", [401, 404, 409, 503, 307])
async def test_http_failure_not_retried_and_no_credentials_in_error(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, json={"detail": TOKEN}, headers={"Location": "https://other.invalid"}
        )

    async with DeviceAPI(
        "https://center.test", TOKEN, transport=httpx.MockTransport(handle)
    ) as api:
        with pytest.raises(DeviceAPIError) as caught:
            await api.submit(op())
    assert len(calls) == 1
    assert TOKEN not in str(caught.value)
    assert caught.value.code == (
        "operation_result_uncertain" if status >= 500 else "center_http_" + str(status)
    )


async def test_wrong_result_location_is_rejected():
    response = record("succeeded")
    response["result"] = {
        "location": {"namespace": "server", "device_id": "laptop-one"},
        "data": {"success": True},
    }
    async with DeviceAPI(
        "https://center.test",
        TOKEN,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response)),
    ) as api:
        with pytest.raises(DeviceAPIError) as caught:
            await api.submit(op())
    assert caught.value.code == "device_result_location_mismatch"


async def test_invalid_json_is_uncertain_after_post():
    async with DeviceAPI(
        "https://center.test",
        TOKEN,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="not json")),
    ) as api:
        with pytest.raises(DeviceAPIError) as caught:
            await api.submit(op())
    assert caught.value.code == "operation_result_uncertain"


async def test_devices_bounded_without_post():
    devices = [{"device_id": f"device-{n}", "online": False, "capabilities": []} for n in range(9)]
    async with DeviceAPI(
        "https://center.test",
        TOKEN,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=json.dumps(devices))),
    ) as api:
        result = await api.list_devices(offset=2, limit=3)
    assert len(result["devices"]) == 3 and result["next_offset"] == 5


def test_client_host_mount_url_supported_without_arbitrary_path():
    from device_runtime.client import device_url

    assert (
        device_url("wss://center.test/device-runtime", "laptop-one")
        == "wss://center.test/device-runtime/v1/devices/laptop-one/connect"
    )
    assert (
        device_url("ws://localhost:3333/device-runtime/", "laptop-one")
        == "ws://localhost:3333/device-runtime/v1/devices/laptop-one/connect"
    )
    with pytest.raises(ValueError):
        device_url("wss://center.test/arbitrary", "laptop-one")


async def test_wait_parameter_is_transport_only():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=record("sent"))

    operation = op()
    digest = operation.digest
    async with DeviceAPI("http://127.0.0.1:1", TOKEN, transport=httpx.MockTransport(handle)) as api:
        await api.submit(operation, wait_timeout_ms=1500)
    assert calls[0].url.params["wait_timeout_ms"] == "1500"
    assert json.loads(calls[0].content) == operation.model_dump()
    assert operation.digest == digest
