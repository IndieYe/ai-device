"""Thin stdio Device MCP on the center; no local Bash/FS or device execution imports."""

import base64
import os
import sys
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import Field

from .http_client import DeviceAPI
from .protocol import MAX_IMAGE_BYTES, Edit, Identifier, ImagePayload, Operation, canonical
from .version import runtime_version

DevicePath = Annotated[
    str,
    Field(
        min_length=1, max_length=4096, description="Absolute POSIX device path; never center path"
    ),
]


def operation_result(receipt: dict) -> CallToolResult:
    """Keep image bytes out of text/structured summaries; never open device paths here."""
    result = receipt.get("result") or {}
    data = result.get("data") or {}
    image = data.get("image")
    content = []
    if image is not None:
        payload = ImagePayload.model_validate(image)
        raw = base64.b64decode(payload.base64, validate=True)
        if len(raw) != payload.size:
            raise ValueError("image payload size mismatch")
        metadata = payload.model_dump(exclude={"base64"})
        receipt = {
            **receipt,
            "result": {**result, "data": {**data, "image": metadata}},
        }
        content.append(ImageContent(type="image", data=payload.base64, mimeType=payload.mime_type))
    content.insert(0, TextContent(type="text", text=canonical(receipt)))
    return CallToolResult(content=content, structuredContent=receipt)


def create_mcp(url: str, token: str):
    api = DeviceAPI(url, token)

    @asynccontextmanager
    async def lifespan(_):
        async with api:
            yield {}

    mcp = FastMCP("device_mcp", lifespan=lifespan)
    # FastMCP 1.x does not expose version in its constructor.
    mcp._mcp_server.version = runtime_version()["version"]
    read_only = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
    action = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True}

    @mcp.tool(annotations=read_only)
    async def get_version() -> dict:
        """Read running MCP and center software identities; no upgrade or execution."""
        return {"mcp": runtime_version(), "center": await api.get_version()}

    @mcp.tool(annotations=read_only)
    async def list_devices(
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=100)] = 40,
    ) -> dict:
        """List paired devices/online state/capabilities. No local fallback; bounded pagination."""
        return await api.list_devices(offset=offset, limit=limit)

    @mcp.tool(annotations=read_only)
    async def get_operation(device_id: Identifier, operation_id: Identifier) -> CallToolResult:
        """Query the same operation, including standard image content when available.

        Text metadata retains the device namespace. unknown is not permission to retry.
        """
        return operation_result(await api.get_operation(device_id, operation_id))

    async def submit(device_id, operation_id, kind, arguments, *, wait_timeout_ms=0):
        return await api.submit(
            Operation(
                device_id=device_id, operation_id=operation_id, action=kind, arguments=arguments
            ),
            wait_timeout_ms=wait_timeout_ms,
        )

    @mcp.tool(annotations=action)
    async def run(
        device_id: Identifier,
        operation_id: Identifier,
        argv: Annotated[list[str], Field(min_length=1, max_length=256)],
        cwd: DevicePath,
        timeout: Annotated[float, Field(strict=True, gt=0, le=300)] = 60,
        bytes: Annotated[int, Field(strict=True, ge=0, le=131072)] = 8192,
        timeout_behavior: Literal["background", "kill"] = "background",
        stdin: Annotated[str | None, Field(max_length=262144)] = None,
        purpose: Annotated[str | None, Field(max_length=1000)] = None,
    ) -> dict:
        """Execute argv on the explicit device. timeout is foreground wait; default continues in background.

        Keep operation_id for recovery; never replay uncertain execution under a new ID.
        Paths belong to device. Use get with execution_id=operation_id, not a PID.
        """
        return await api.execute(
            device_id,
            operation_id,
            {
                "mode": "run",
                "argv": argv,
                "cwd": cwd,
                "timeout": timeout,
                "bytes": bytes,
                "timeout_behavior": timeout_behavior,
                "stdin": stdin,
                "purpose": purpose,
            },
        )

    @mcp.tool(annotations=action)
    async def bash(
        device_id: Identifier,
        operation_id: Identifier,
        command: str,
        cwd: DevicePath,
        timeout: Annotated[float, Field(strict=True, gt=0, le=300)] = 60,
        bytes: Annotated[int, Field(strict=True, ge=0, le=131072)] = 8192,
        timeout_behavior: Literal["background", "kill"] = "background",
        purpose: str | None = None,
    ) -> dict:
        """Run a Bash command on device, with the same background/timeout semantics as run."""
        return await api.execute(
            device_id,
            operation_id,
            {
                "mode": "bash",
                "command": command,
                "cwd": cwd,
                "timeout": timeout,
                "bytes": bytes,
                "timeout_behavior": timeout_behavior,
                "purpose": purpose,
            },
        )

    @mcp.tool(annotations=action)
    async def script(
        device_id: Identifier,
        operation_id: Identifier,
        script: str,
        cwd: DevicePath,
        timeout: Annotated[float, Field(strict=True, gt=0, le=300)] = 60,
        bytes: Annotated[int, Field(strict=True, ge=0, le=131072)] = 8192,
        timeout_behavior: Literal["background", "kill"] = "background",
        stdin: Annotated[str | None, Field(max_length=262144)] = None,
        purpose: str | None = None,
    ) -> dict:
        """Run a managed Bash script after bash -n. Script and stdin are separate device artifacts."""
        return await api.execute(
            device_id,
            operation_id,
            {
                "mode": "script",
                "script": script,
                "cwd": cwd,
                "timeout": timeout,
                "bytes": bytes,
                "timeout_behavior": timeout_behavior,
                "stdin": stdin,
                "purpose": purpose,
            },
        )

    @mcp.tool(annotations=read_only)
    async def get(
        device_id: Identifier,
        execution_id: Identifier,
        timeout: Annotated[float, Field(strict=True, ge=0, le=300)] = 0,
        bytes: Annotated[int, Field(strict=True, ge=0, le=131072)] = 200,
    ) -> dict:
        """Get current execution status and output tail. timeout only waits; never cancels execution.

        execution_id is the original operation_id. Output paths are on device.
        """
        return await api.get_execution(device_id, execution_id, timeout=timeout, output_bytes=bytes)

    @mcp.tool(annotations={**read_only, "idempotentHint": True})
    async def read_file(
        device_id: Identifier,
        operation_id: Identifier,
        path: DevicePath,
        max_chars: Annotated[int, Field(strict=True, ge=1, le=131072)] = 10000,
        type: Literal["chars", "lines"] = "chars",
        offset: Annotated[int, Field(strict=True, ge=0)] = 0,
        limit: Annotated[int | None, Field(strict=True, ge=0)] = None,
        direction: Literal["forward", "backward"] = "forward",
    ) -> dict:
        """Read absolute device path. Requires stable ID; query receipt, never use center FS on path."""
        return await submit(
            device_id,
            operation_id,
            "fs.read_file",
            {
                "path": path,
                "max_chars": max_chars,
                "type": type,
                "offset": offset,
                "limit": limit,
                "direction": direction,
            },
        )

    @mcp.tool(annotations=action)
    async def write_file(
        device_id: Identifier,
        operation_id: Identifier,
        path: DevicePath,
        content: Annotated[str, Field(max_length=262144)],
    ) -> dict:
        """Write absolute device path. Stable ID deduplicates; unknown/failed may have side effects."""
        return await submit(
            device_id, operation_id, "fs.write_file", {"path": path, "content": content}
        )

    @mcp.tool(annotations=action)
    async def edit_file(
        device_id: Identifier,
        operation_id: Identifier,
        path: DevicePath,
        edits: Annotated[list[Edit], Field(min_length=1, max_length=64)],
        dry_run: Annotated[bool, Field(strict=True)] = False,
        include_diff: Annotated[bool, Field(strict=True)] = False,
    ) -> dict:
        """Exact replacements only on explicit device path. Stable ID required; never blindly retry."""
        return await submit(
            device_id,
            operation_id,
            "fs.edit_file",
            {
                "path": path,
                "edits": [edit.model_dump() for edit in edits],
                "dry_run": dry_run,
                "include_diff": include_diff,
            },
        )

    @mcp.tool(annotations=action)
    async def insert_file(
        device_id: Identifier,
        operation_id: Identifier,
        path: DevicePath,
        text: Annotated[str, Field(max_length=262144)],
        line: Annotated[int | None, Field(strict=True, ge=1)] = None,
        before: Annotated[str | None, Field(min_length=1, max_length=65536)] = None,
        after: Annotated[str | None, Field(min_length=1, max_length=65536)] = None,
        at_end: Annotated[bool, Field(strict=True)] = False,
        dry_run: Annotated[bool, Field(strict=True)] = False,
        include_diff: Annotated[bool, Field(strict=True)] = False,
    ) -> dict:
        """Insert at exactly one of line/before/after/at_end on the explicit device.

        dry_run previews without writing. Every changed request needs its own stable ID;
        never change ID to retry an uncertain execution. Diff may be omitted by result limits.
        """
        return await submit(
            device_id,
            operation_id,
            "fs.insert_file",
            {
                "path": path,
                "text": text,
                "line": line,
                "before": before,
                "after": after,
                "at_end": at_end,
                "dry_run": dry_run,
                "include_diff": include_diff,
            },
        )

    @mcp.tool(annotations=read_only)
    async def read_image(
        device_id: Identifier,
        operation_id: Identifier,
        path: DevicePath,
        max_bytes: Annotated[int, Field(strict=True, ge=1, le=MAX_IMAGE_BYTES)] = MAX_IMAGE_BYTES,
    ) -> CallToolResult:
        """Read PNG/JPEG/WebP on the explicit device (up to 5 MiB), never center FS.

        Returns acceptance, not a completion promise. Poll get_operation with the same
        ID to receive standard MCP image content. No resizing or silent format conversion.
        """
        return operation_result(
            await submit(
                device_id, operation_id, "fs.read_image", {"path": path, "max_bytes": max_bytes}
            )
        )

    return mcp


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="device-mcp")
    parser.add_argument("--version", action="version", version=json.dumps(runtime_version()))
    parser.parse_args()
    try:
        mcp = create_mcp(
            os.environ.get("DEVICE_RUNTIME_API_URL", ""),
            os.environ.get("DEVICE_RUNTIME_ADMIN_TOKEN", ""),
        )
    except ValueError:
        print(
            "Configure DEVICE_RUNTIME_API_URL and a valid DEVICE_RUNTIME_ADMIN_TOKEN",
            file=sys.stderr,
        )
        return 2
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
