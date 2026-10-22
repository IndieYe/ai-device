"""Protocol-independent local image reading."""

from __future__ import annotations

from ..image_utils import (
    DEFAULT_MAX_IMAGE_BYTES,
    ImageReadError,
    validate_image_bytes,
    validate_max_bytes,
)
from ..path_utils import validate_path
from .results import ImageResult


def read_image(
    path: str,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> ImageResult:
    """Read a local image and return validated bytes and metadata.

    Args:
        path: Image path on the machine running this server.
        max_bytes: Per-call size limit. Defaults to 5 MB and cannot exceed 5 MB.

    Returns:
        ImageResult containing an ImageInfo and the resolved path.
        Raises ImageReadError on failure.
    """
    try:
        validate_max_bytes(max_bytes)

        file_path = validate_path(path)
        if not file_path.exists():
            raise ImageReadError(
                "IMAGE_NOT_FOUND", "Image file was not found", {"path": str(file_path)}
            )
        if not file_path.is_file():
            raise ImageReadError(
                "IMAGE_NOT_A_FILE", "Image path is not a file", {"path": str(file_path)}
            )

        size = file_path.stat().st_size
        if size == 0:
            raise ImageReadError(
                "IMAGE_EMPTY", "Image file is empty", {"path": str(file_path)}
            )
        if size > max_bytes:
            raise ImageReadError(
                "IMAGE_TOO_LARGE",
                "Image exceeds the allowed size",
                {"path": str(file_path), "size": size, "max_bytes": max_bytes},
            )

        try:
            with file_path.open("rb") as file_obj:
                data = file_obj.read(max_bytes + 1)
        except OSError as exc:
            raise ImageReadError(
                "IMAGE_READ_ERROR",
                "Failed to read image file",
                {"path": str(file_path), "reason": str(exc)},
            ) from exc

        if len(data) > max_bytes:
            raise ImageReadError(
                "IMAGE_TOO_LARGE",
                "Image exceeds the allowed size",
                {"path": str(file_path), "size": len(data), "max_bytes": max_bytes},
            )

        info = validate_image_bytes(data, path=str(file_path), max_bytes=max_bytes)
        return ImageResult(info=info, path=str(file_path))
    except ImageReadError:
        raise
    except OSError as exc:
        raise ImageReadError(
            "IMAGE_READ_ERROR",
            "Failed to access image file",
            {"path": path, "reason": str(exc)},
        ) from exc
    except Exception as exc:
        raise ImageReadError(
            "IMAGE_READ_ERROR",
            "Failed to read image file",
            {"path": path, "reason": str(exc)},
        ) from exc

    raise AssertionError("unreachable")
