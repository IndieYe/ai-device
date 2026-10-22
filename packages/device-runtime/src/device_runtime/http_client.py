"""Center-only HTTP adapter. Never executes locally or automatically retries a POST."""

import asyncio
import ipaddress
import time
import uuid
from urllib.parse import urlsplit

import httpx
from pydantic import TypeAdapter

from .protocol import MAX_MESSAGE_BYTES, Identifier, Operation


class DeviceAPIError(RuntimeError):
    def __init__(self, code: str, operation_id: str | None = None):
        self.code = code
        self.operation_id = operation_id
        self.execution_id = None
        message = code
        if operation_id is not None:
            message += f"; reconcile operation_id={operation_id}; never change ID or blindly replay"
        super().__init__(message)


class DeviceAPI:
    def __init__(self, url: str, token: str, *, transport=None):
        parts = urlsplit(url)
        try:
            loopback = ipaddress.ip_address(parts.hostname or "").is_loopback
        except ValueError:
            loopback = parts.hostname == "localhost"
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or (parts.scheme == "http" and not loopback)
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError("center URL requires HTTPS or loopback HTTP, without URL credentials")
        if len(token) < 16 or not token.isascii() or not token.isprintable():
            raise ValueError("configure printable ASCII center token with at least 16 characters")
        self._http = httpx.AsyncClient(
            base_url=url.rstrip("/") + "/",
            headers={"Authorization": "Bearer " + token},
            timeout=10,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._http.aclose()

    async def _request(self, method, path, *, payload=None, operation_id=None):
        uncertain = method == "POST"
        try:
            async with self._http.stream(method, path, json=payload) as response:
                if response.status_code != 200:
                    code = (
                        "operation_result_uncertain"
                        if uncertain and response.status_code >= 500
                        else f"center_http_{response.status_code}"
                    )
                    raise DeviceAPIError(code, operation_id)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_MESSAGE_BYTES:
                        raise ValueError("response exceeds bound")
                import json

                return json.loads(content)
        except (httpx.HTTPError, ValueError):
            code = "operation_result_uncertain" if uncertain else "center_response_unavailable"
            raise DeviceAPIError(code, operation_id) from None

    @staticmethod
    def _record(value, device_id, operation_id):
        if not isinstance(value, dict) or value.get("operation_id") != operation_id:
            raise DeviceAPIError("operation_response_mismatch", operation_id)
        if value.get("device_id") != device_id:
            raise DeviceAPIError("device_target_mismatch", operation_id)
        if value.get("state") not in (
            "dispatching",
            "sent",
            "running",
            "succeeded",
            "failed",
            "unknown",
        ):
            raise DeviceAPIError("operation_state_invalid", operation_id)
        result = value.get("result")
        if result is not None and (
            not isinstance(result, dict)
            or result.get("location") != {"namespace": "device", "device_id": device_id}
        ):
            raise DeviceAPIError("device_result_location_mismatch", operation_id)
        # Do not echo the potentially large original argv/file payload from the journal.
        return {
            "operation_id": operation_id,
            "device_id": device_id,
            "state": value["state"],
            "result": result,
            "recovery": "Query the same ID; unknown/failed may have side effects. Never blindly replay.",
        }

    async def submit(self, operation: Operation, *, wait_timeout_ms: int = 0):
        if type(wait_timeout_ms) is not int or not 0 <= wait_timeout_ms <= 5000:
            raise ValueError("wait_timeout_ms must be an integer between 0 and 5000")
        value = await self._request(
            "POST",
            "v1/operations" + (f"?wait_timeout_ms={wait_timeout_ms}" if wait_timeout_ms else ""),
            payload=operation.model_dump(),
            operation_id=operation.operation_id,
        )
        return self._record(value, operation.device_id, operation.operation_id)

    async def execute(self, device_id, operation_id, arguments):
        operation = Operation(
            device_id=device_id,
            operation_id=operation_id,
            action="bash.execute",
            arguments=arguments,
        )
        receipt = await self.submit(operation, wait_timeout_ms=0)
        # Each short GET is below the HTTP transport deadline. Never retry POST.
        deadline = time.monotonic() + operation.arguments["timeout"] + 2
        while (
            receipt["state"] not in {"succeeded", "failed", "unknown"}
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.1)
            receipt = await self.get_operation(device_id, operation_id)
        return receipt

    async def get_execution(self, device_id, execution_id, *, timeout=0, output_bytes=200):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 <= timeout <= 300
        ):
            raise ValueError("timeout must be between 0 and 300 seconds")
        deadline = time.monotonic() + timeout
        operation_id = None

        receipt = None

        async def bounded_request(awaitable):
            # Always obtain the first receipt with the normal HTTP transport budget.
            # A command wait deadline must not cancel its initial acceptance.
            if receipt is None or timeout == 0:
                return await awaitable
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                return await awaitable

        try:
            while True:
                operation_id = "query-" + uuid.uuid4().hex
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                receipt = await bounded_request(
                    self.submit(
                        Operation(
                            device_id=device_id,
                            operation_id=operation_id,
                            action="bash.get",
                            arguments={"execution_id": execution_id, "bytes": output_bytes},
                        ),
                        # Initial submission is immediate, not a server-side long poll.
                        wait_timeout_ms=min(5000, remaining_ms) if receipt is not None else 0,
                    )
                )
                while (
                    receipt["state"] not in {"succeeded", "failed", "unknown"}
                    and time.monotonic() < deadline
                ):
                    await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
                    if time.monotonic() >= deadline:
                        return receipt
                    receipt = await bounded_request(self.get_operation(device_id, operation_id))
                result = receipt.get("result") or {}
                data = result.get("data") or {}
                if data.get("state") != "running" or time.monotonic() >= deadline:
                    return receipt
                await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
                if time.monotonic() >= deadline:
                    return receipt
        except TimeoutError:
            # A refresh may be interrupted at the command deadline; the last
            # accepted receipt remains valid. Never resubmit the execution.
            return receipt
        except DeviceAPIError as exc:
            exc.execution_id = execution_id
            raise

    async def get_operation(self, device_id: str, operation_id: str):
        adapter = TypeAdapter(Identifier)
        adapter.validate_python(device_id)
        adapter.validate_python(operation_id)
        value = await self._request(
            "GET", "v1/operations/" + operation_id, operation_id=operation_id
        )
        return self._record(value, device_id, operation_id)

    async def get_version(self):
        from .version import RuntimeVersion

        value = await self._request("GET", "v1/version")
        if not isinstance(value, dict) or value.get("role") != "center":
            raise DeviceAPIError("center_version_invalid")
        try:
            identity = RuntimeVersion.model_validate(
                {k: v for k, v in value.items() if k != "role"}
            )
        except ValueError as exc:
            raise DeviceAPIError("center_version_invalid") from exc
        return {"role": "center", **identity.model_dump()}

    async def list_devices(self, *, offset=0, limit=40):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset >= 0; limit between 1 and 100")
        value = await self._request("GET", "v1/devices")
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise DeviceAPIError("device_directory_invalid")
        end = offset + limit
        return {"devices": value[offset:end], "next_offset": end if end < len(value) else None}
