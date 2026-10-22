"""Bounded images from real paired FS core through stdio, HTTP and WebSocket."""

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import pytest
from conftest import running_client
from PIL import Image
from pydantic import ValidationError

from device_runtime.executor import LocalExecutor
from device_runtime.protocol import MAX_IMAGE_BYTES, MAX_OPERATION_BYTES, Operation, canonical


def image_operation(file, **args):
    return Operation(
        device_id="laptop-one",
        operation_id="image-one",
        action="fs.read_image",
        arguments={"path": str(file), **args},
    )


@pytest.mark.parametrize(
    "format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
async def test_core_image_metadata_and_original_bytes(tmp_path, format, mime):
    file = tmp_path / "中文 图像"
    Image.new("RGB", (8, 9), "blue").save(file, format=format)
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    try:
        state, result = await executor(image_operation(file))
        assert state == "succeeded"
        payload = result["data"]["image"]
        assert payload["mime_type"] == mime
        assert payload["width"] == 8 and payload["height"] == 9
        assert payload["size"] == file.stat().st_size
        assert base64.b64decode(payload["base64"]) == file.read_bytes()
        assert result["location"] == {"namespace": "device", "device_id": "laptop-one"}
    finally:
        await executor.aclose()


@pytest.mark.parametrize("case", ["missing", "empty", "invalid", "unsupported", "too_large"])
async def test_image_failures_are_explicit(tmp_path, case):
    file = tmp_path / "image"
    args = {}
    if case == "empty":
        file.touch()
    elif case == "invalid":
        file.write_text("not a picture")
    elif case == "unsupported":
        Image.new("RGB", (3, 3)).save(file, format="GIF")
    elif case == "too_large":
        Image.new("RGB", (3, 3)).save(file, format="PNG")
        args["max_bytes"] = file.stat().st_size - 1
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    try:
        state, result = await executor(image_operation(file, **args))
        assert state == "failed" and result["data"]["success"] is False
        assert result["data"]["code"].startswith("IMAGE_")
        assert "image" not in result["data"]
    finally:
        await executor.aclose()


@pytest.mark.parametrize("max_bytes", [True, "1", 1.0, 0, MAX_IMAGE_BYTES + 1])
def test_image_argument_budget(tmp_path, max_bytes):
    with pytest.raises(ValidationError):
        image_operation(tmp_path / "image", max_bytes=max_bytes)


def test_image_result_budget_does_not_expand_operation_budget():
    with pytest.raises(ValidationError):
        Operation(
            device_id="one",
            operation_id="one",
            action="fs.write_file",
            arguments={"path": "/tmp/f", "content": "中" * 262144},
        )
    assert MAX_OPERATION_BYTES == 524288


async def test_real_stdio_image_content_budget_and_stable_receipt(live_server, tmp_path):
    mcp = pytest.importorskip("mcp")
    stdio_client = pytest.importorskip("mcp.client.stdio").stdio_client
    url, service, http = live_server
    state_dir = tmp_path / "client"
    executor = LocalExecutor(state_dir, "laptop-one")
    file = tmp_path / "large-image.png"
    Image.frombytes("RGB", (640, 640), os.urandom(640 * 640 * 3)).save(file)
    original = file.read_bytes()
    assert len(original) > 1_048_576
    params = mcp.StdioServerParameters(
        command=sys.executable,
        args=["-m", "device_runtime.mcp_server"],
        env={
            "DEVICE_RUNTIME_API_URL": url.replace("ws://", "http://"),
            "DEVICE_RUNTIME_ADMIN_TOKEN": "test-only-admin-token-not-a-secret",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    async with (
        running_client(live_server, state_dir, executor),
        stdio_client(params) as (read, write),
        mcp.ClientSession(read, write) as session,
    ):
        await session.initialize()
        request = {"device_id": "laptop-one", "operation_id": "image-large", "path": str(file)}
        submitted = await session.call_tool("read_image", request)
        assert not submitted.isError, submitted.content
        async with asyncio.timeout(10):
            while True:
                response = await session.call_tool(
                    "get_operation",
                    {
                        "device_id": "laptop-one",
                        "operation_id": "image-large",
                    },
                )
                assert not response.isError, response.content
                receipt = response.structuredContent
                if receipt["state"] == "succeeded":
                    break
                await asyncio.sleep(0.01)
        assert receipt["result"]["location"] == {"namespace": "device", "device_id": "laptop-one"}
        assert "base64" not in canonical(receipt)
        images = [c for c in response.content if c.type == "image"]
        assert len(images) == 1 and images[0].mimeType == "image/png"
        assert base64.b64decode(images[0].data) == original
        for block in response.content:
            if block.type == "text":
                assert len(block.text) < 2000 and "base64" not in block.text
        # Reusing the exact accepted request returns the original bytes even after
        # the fixture file changes: no implicit fresh read and no host FS fallback.
        file.write_text("now not an image")
        repeated = await session.call_tool("read_image", request)
        assert not repeated.isError
        assert (
            base64.b64decode(next(c.data for c in repeated.content if c.type == "image"))
            == original
        )
        wrong_target = await session.call_tool(
            "get_operation",
            {
                "device_id": "other-device",
                "operation_id": "image-large",
            },
        )
        assert wrong_target.isError
        bad = await session.call_tool(
            "read_image", {**request, "operation_id": "image-invalid", "max_bytes": True}
        )
        assert bad.isError and (await http.get("/v1/operations/image-invalid")).status_code == 404
        saved = await service.journal.get("image-large")
        assert saved["state"] == "succeeded"
        assert len(json.dumps(saved["result"])) > 1_048_576
