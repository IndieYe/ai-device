"""Translate protocol-independent results to the existing MCP contracts."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import NoReturn

from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolResult
from mcp.types import ImageContent, TextContent

from ..core.results import FileResult, ImageResult
from ..image_utils import ImageReadError
from ..responses import json_response


def to_tool_result(result: FileResult) -> ToolResult:
    return ToolResult(content=json_response(result.data), meta=result.metadata)


def to_image_tool_result(result: ImageResult) -> ToolResult:
    info = result.info
    file_name = Path(result.path).name or result.path
    summary = f"Image {file_name}: {info.mime_type}, {info.width}×{info.height}, {info.size} bytes"
    metadata = {
        "path": result.path,
        "mime_type": info.mime_type,
        "format": info.format,
        "width": info.width,
        "height": info.height,
        "size": info.size,
        "animated": info.animated,
    }
    return ToolResult(
        content=[
            TextContent(type="text", text=summary),
            ImageContent(
                type="image",
                data=base64.b64encode(info.data).decode("ascii"),
                mimeType=info.mime_type,
            ),
        ],
        structured_content=metadata,
        meta={
            "summary": f"read image {file_name}",
            **metadata,
            "execution_mode": "local",
        },
    )


def raise_image_tool_error(error: ImageReadError) -> NoReturn:
    payload = {
        "success": False,
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        },
    }
    raise ToolError(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
