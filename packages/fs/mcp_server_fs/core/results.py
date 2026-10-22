"""Protocol-independent local operation results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..image_utils import ImageInfo


@dataclass
class FileResult:
    """Plain operation payload plus concise metadata, without JSON/MCP wrapping."""

    data: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImageResult:
    """Validated original image bytes and their local source path."""

    info: ImageInfo
    path: str
