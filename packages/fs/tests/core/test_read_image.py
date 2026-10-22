"""Tests for local image reading and validation."""

from __future__ import annotations

import base64
import inspect
import io
import json
from pathlib import Path
from unittest.mock import patch

import anyio
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.types import ImageContent, TextContent
from PIL import Image

from mcp_server_fs.image_utils import HARD_MAX_IMAGE_BYTES
from mcp_server_fs.server import create_mcp
from mcp_server_fs.tools.read_file import read_file
from mcp_server_fs.tools.read_image import read_image


def parse_tool_error(exc: ToolError) -> dict:
    return json.loads(str(exc))


def save_image(path: Path, image_format: str, *, size: tuple[int, int] = (3, 2)) -> bytes:
    image = Image.new("RGB", size, color=(20, 40, 60))
    image.save(path, format=image_format)
    return path.read_bytes()


@pytest.mark.parametrize(
    ("image_format", "suffix", "mime_type"),
    [("PNG", ".png", "image/png"), ("JPEG", ".jpg", "image/jpeg"), ("WEBP", ".webp", "image/webp")],
)
def test_read_image_returns_text_image_and_metadata(tmp_path, image_format, suffix, mime_type):
    target = tmp_path / f"sample{suffix}"
    raw = save_image(target, image_format)

    result = read_image(str(target))

    assert len(result.content) == 2
    assert isinstance(result.content[0], TextContent)
    assert isinstance(result.content[1], ImageContent)
    assert result.content[1].mimeType == mime_type
    assert base64.b64decode(result.content[1].data, validate=True) == raw
    assert result.structured_content == {
        "path": str(target.resolve()),
        "mime_type": mime_type,
        "format": image_format,
        "width": 3,
        "height": 2,
        "size": len(raw),
        "animated": False,
    }
    assert result.meta["execution_mode"] == "local"
    assert result.meta["summary"] == f"read image {target.name}"
    assert result.content[1].data not in result.content[0].text
    assert result.content[1].data not in json.dumps(result.structured_content)
    assert result.content[1].data not in json.dumps(result.meta)


def test_read_image_uses_file_content_not_extension(tmp_path):
    target = tmp_path / "looks-like-jpeg.jpg"
    save_image(target, "PNG")

    result = read_image(str(target))

    assert result.content[1].mimeType == "image/png"
    assert result.structured_content["format"] == "PNG"


def test_read_image_rejects_animated_webp(tmp_path):
    target = tmp_path / "animated.webp"
    frames = [Image.new("RGB", (2, 2), color=color) for color in ("red", "blue")]
    frames[0].save(target, format="WEBP", save_all=True, append_images=frames[1:], duration=50, loop=0)

    with pytest.raises(ToolError) as caught:
        read_image(str(target))

    assert parse_tool_error(caught.value)["error"]["code"] == "IMAGE_ANIMATED_UNSUPPORTED"


def test_read_image_rejects_apng(tmp_path):
    target = tmp_path / "animated.png"
    frames = [Image.new("RGBA", (2, 2), color=color) for color in ("red", "blue")]
    frames[0].save(target, format="PNG", save_all=True, append_images=frames[1:], duration=50, loop=0)

    with pytest.raises(ToolError) as caught:
        read_image(str(target))

    assert parse_tool_error(caught.value)["error"]["code"] == "IMAGE_ANIMATED_UNSUPPORTED"


@pytest.mark.parametrize("image_format", ["GIF", "BMP", "TIFF"])
def test_read_image_rejects_unsupported_formats(tmp_path, image_format):
    target = tmp_path / f"unsupported-{image_format.lower()}"
    save_image(target, image_format)

    with pytest.raises(ToolError) as caught:
        read_image(str(target))

    payload = parse_tool_error(caught.value)
    assert payload["error"]["code"] == "IMAGE_FORMAT_UNSUPPORTED"
    assert payload["error"]["details"]["format"] == image_format


@pytest.mark.parametrize(
    ("name", "content", "image_format"),
    [
        ("vector.svg", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "SVG"),
        ("document.pdf", b"%PDF-1.7\n", "PDF"),
    ],
)
def test_read_image_rejects_known_non_raster_formats(tmp_path, name, content, image_format):
    target = tmp_path / name
    target.write_bytes(content)

    with pytest.raises(ToolError) as caught:
        read_image(str(target))

    payload = parse_tool_error(caught.value)
    assert payload["error"]["code"] == "IMAGE_FORMAT_UNSUPPORTED"
    assert payload["error"]["details"]["format"] == image_format


def test_read_image_rejects_corrupt_image(tmp_path):
    target = tmp_path / "corrupt.png"
    target.write_bytes(b"not-an-image")

    with pytest.raises(ToolError) as caught:
        read_image(str(target))

    assert parse_tool_error(caught.value)["error"]["code"] == "IMAGE_INVALID"


def test_read_image_rejects_truncated_image(tmp_path):
    target = tmp_path / "truncated.png"
    raw = save_image(target, "PNG", size=(40, 40))
    target.write_bytes(raw[:-12])

    with pytest.raises(ToolError) as caught:
        read_image(str(target))

    assert parse_tool_error(caught.value)["error"]["code"] == "IMAGE_INVALID"


def test_read_image_rejects_truncated_jpeg(tmp_path):
    target = tmp_path / "truncated.jpg"
    raw = save_image(target, "JPEG", size=(40, 40))
    target.write_bytes(raw[:-2])

    with pytest.raises(ToolError) as caught:
        read_image(str(target))

    assert parse_tool_error(caught.value)["error"]["code"] == "IMAGE_INVALID"


@pytest.mark.parametrize(
    ("target_kind", "code"),
    [("missing", "IMAGE_NOT_FOUND"), ("directory", "IMAGE_NOT_A_FILE"), ("empty", "IMAGE_EMPTY")],
)
def test_read_image_rejects_invalid_paths(tmp_path, target_kind, code):
    target = tmp_path / target_kind
    if target_kind == "directory":
        target.mkdir()
    elif target_kind == "empty":
        target.touch()

    with pytest.raises(ToolError) as caught:
        read_image(str(target))

    assert parse_tool_error(caught.value)["error"]["code"] == code


def test_read_image_rejects_file_over_call_limit_before_read(tmp_path, monkeypatch):
    target = tmp_path / "large.png"
    target.write_bytes(b"1234")

    def fail_open(_self, *_args, **_kwargs):
        raise AssertionError("file content should not be read after stat rejects it")

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(ToolError) as caught:
        read_image(str(target), max_bytes=3)

    assert parse_tool_error(caught.value)["error"]["code"] == "IMAGE_TOO_LARGE"


def test_read_image_limits_actual_read_after_file_growth(tmp_path, monkeypatch):
    target = tmp_path / "growing.png"
    target.write_bytes(b"12")
    read_sizes: list[int] = []

    class TrackingBytesIO(io.BytesIO):
        def read(self, size=-1):
            read_sizes.append(size)
            return super().read(size)

    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: TrackingBytesIO(b"123456789"))
    with pytest.raises(ToolError) as caught:
        read_image(str(target), max_bytes=3)

    assert read_sizes == [4]
    assert parse_tool_error(caught.value)["error"]["code"] == "IMAGE_TOO_LARGE"


@pytest.mark.parametrize("max_bytes", [0, -1, True, HARD_MAX_IMAGE_BYTES + 1])
def test_read_image_rejects_invalid_max_bytes(tmp_path, max_bytes):
    target = tmp_path / "sample.png"
    save_image(target, "PNG")

    with pytest.raises(ToolError) as caught:
        read_image(str(target), max_bytes=max_bytes)

    assert parse_tool_error(caught.value)["error"]["code"] == "IMAGE_INVALID_ARGUMENT"


def test_read_image_rejects_excessive_pixels(tmp_path, monkeypatch):
    target = tmp_path / "pixels.png"
    save_image(target, "PNG", size=(3, 2))
    monkeypatch.setattr("mcp_server_fs.image_utils.MAX_IMAGE_PIXELS", 5)

    with pytest.raises(ToolError) as caught:
        read_image(str(target))

    payload = parse_tool_error(caught.value)
    assert payload["error"]["code"] == "IMAGE_TOO_MANY_PIXELS"
    assert payload["error"]["details"]["pixels"] == 6




def test_read_image_and_read_file_schemas_are_narrowed():
    assert list(inspect.signature(read_image).parameters) == ["path", "max_bytes"]
    assert "bytes" not in str(inspect.signature(read_file).parameters["type"].annotation)


def test_read_file_direct_call_rejects_removed_bytes_type(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("hello", encoding="utf-8")

    result = read_file(str(target), type="bytes")  # type: ignore[arg-type]
    payload = json.loads(result.content[0].text)

    assert payload["error"] == "ValueError"
    assert payload["message"] == "Unsupported read type: bytes"


def test_read_image_is_registered_with_server():
    _, tools = create_mcp()

    assert "read_image" in tools


def test_read_image_protocol_success_and_error(tmp_path):
    target = tmp_path / "protocol.png"
    raw = save_image(target, "PNG")
    mcp, _ = create_mcp()

    async def call_tools():
        async with Client(mcp) as client:
            success = await client.call_tool("read_image", {"path": str(target)})
            failure = await client.call_tool(
                "read_image",
                {"path": str(tmp_path / "missing.png")},
                raise_on_error=False,
            )
            return success, failure

    success, failure = anyio.run(call_tools)
    assert success.is_error is False
    assert isinstance(success.content[1], ImageContent)
    assert base64.b64decode(success.content[1].data, validate=True) == raw
    assert failure.is_error is True
    assert json.loads(failure.content[0].text)["error"]["code"] == "IMAGE_NOT_FOUND"


def test_registered_read_file_schema_excludes_bytes():
    mcp, _ = create_mcp()

    async def list_registered_tools():
        async with Client(mcp) as client:
            return await client.list_tools()

    tools = anyio.run(list_registered_tools)
    read_file_tool = next(tool for tool in tools if tool.name == "read_file")
    read_image_tool = next(tool for tool in tools if tool.name == "read_image")
    assert read_file_tool.inputSchema["properties"]["type"]["enum"] == ["chars", "lines"]
    assert set(read_image_tool.inputSchema["properties"]) == {"path", "max_bytes"}
