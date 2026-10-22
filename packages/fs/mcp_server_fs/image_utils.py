"""Protocol-independent image validation."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError


DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024
HARD_MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000

SUPPORTED_IMAGE_MIME_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}


def _unsupported_format_hint(data: bytes) -> str | None:
    """Identify common non-raster inputs that Pillow may not decode."""
    prefix = data[:1024].lstrip().lower()
    if prefix.startswith(b"%pdf-"):
        return "PDF"
    if prefix.startswith(b"<svg") or (prefix.startswith(b"<?xml") and b"<svg" in prefix):
        return "SVG"
    return None


@dataclass(frozen=True)
class ImageInfo:
    """Validated image bytes and metadata."""

    data: bytes
    mime_type: str
    format: str
    width: int
    height: int
    size: int
    animated: bool


class ImageReadError(Exception):
    """Stable, protocol-independent image read failure."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def validate_max_bytes(max_bytes: int) -> int:
    """Validate the caller-provided image byte limit."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ImageReadError(
            "IMAGE_INVALID_ARGUMENT",
            "max_bytes must be a positive integer",
            {"max_bytes": max_bytes, "hard_max_bytes": HARD_MAX_IMAGE_BYTES},
        )
    if max_bytes > HARD_MAX_IMAGE_BYTES:
        raise ImageReadError(
            "IMAGE_INVALID_ARGUMENT",
            "max_bytes exceeds the hard image size limit",
            {"max_bytes": max_bytes, "hard_max_bytes": HARD_MAX_IMAGE_BYTES},
        )
    return max_bytes


def validate_image_bytes(data: bytes, *, path: str, max_bytes: int) -> ImageInfo:
    """Validate image bytes using Pillow and return authoritative metadata."""
    validate_max_bytes(max_bytes)
    size = len(data)
    if size == 0:
        raise ImageReadError("IMAGE_EMPTY", "Image file is empty", {"path": path})
    if size > max_bytes:
        raise ImageReadError(
            "IMAGE_TOO_LARGE",
            "Image exceeds the allowed size",
            {"path": path, "size": size, "max_bytes": max_bytes},
        )

    try:
        with Image.open(BytesIO(data)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
            animated = bool(getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1)

            mime_type = SUPPORTED_IMAGE_MIME_TYPES.get(image_format)
            if mime_type is None:
                raise ImageReadError(
                    "IMAGE_FORMAT_UNSUPPORTED",
                    "Image format is not supported",
                    {"path": path, "format": image_format or None},
                )
            if animated:
                raise ImageReadError(
                    "IMAGE_ANIMATED_UNSUPPORTED",
                    "Animated images are not supported",
                    {"path": path, "format": image_format},
                )
            pixels = width * height
            if pixels > MAX_IMAGE_PIXELS:
                raise ImageReadError(
                    "IMAGE_TOO_MANY_PIXELS",
                    "Image exceeds the allowed pixel count",
                    {
                        "path": path,
                        "width": width,
                        "height": height,
                        "pixels": pixels,
                        "max_pixels": MAX_IMAGE_PIXELS,
                    },
                )
            image.load()

        with Image.open(BytesIO(data)) as verified_image:
            verified_image.verify()
    except ImageReadError:
        raise
    except Image.DecompressionBombError as exc:
        raise ImageReadError(
            "IMAGE_TOO_MANY_PIXELS",
            "Image exceeds the allowed pixel count",
            {"path": path, "max_pixels": MAX_IMAGE_PIXELS},
        ) from exc
    except UnidentifiedImageError as exc:
        unsupported_format = _unsupported_format_hint(data)
        if unsupported_format is not None:
            raise ImageReadError(
                "IMAGE_FORMAT_UNSUPPORTED",
                "Image format is not supported",
                {"path": path, "format": unsupported_format},
            ) from exc
        raise ImageReadError(
            "IMAGE_INVALID",
            "File is not a valid supported image",
            {"path": path, "reason": str(exc)},
        ) from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise ImageReadError(
            "IMAGE_INVALID",
            "File is not a valid supported image",
            {"path": path, "reason": str(exc)},
        ) from exc

    return ImageInfo(
        data=data,
        mime_type=mime_type,
        format=image_format,
        width=width,
        height=height,
        size=size,
        animated=False,
    )
