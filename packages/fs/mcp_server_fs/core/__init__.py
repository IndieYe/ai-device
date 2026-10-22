"""Reusable local filesystem operations; no MCP or server initialization.

Text operations return FileResult with ordinary dictionaries. Image reads return
ImageResult with original validated bytes and raise ImageReadError on failure.
"""

from ..image_utils import ImageReadError
from .apply_patch import apply_patch
from .edit_file import edit_file
from .insert_file import insert_file
from .read_file import read_file
from .read_image import read_image
from .results import FileResult, ImageResult
from .write_file import write_file

__all__ = [
    "FileResult",
    "ImageReadError",
    "ImageResult",
    "apply_patch",
    "edit_file",
    "insert_file",
    "read_file",
    "read_image",
    "write_file",
]
