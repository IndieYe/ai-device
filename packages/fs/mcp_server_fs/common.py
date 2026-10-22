"""Backward-compatible shared exports.

Prefer importing from the narrower modules in this package for new code.
"""

from .config import DEFAULT_MAX_CHARS, TEMP_DIR
from .formatting import format_size
from .io_utils import atomic_write_text, save_to_temp_file
from .path_utils import normalize_path, validate_path
from .responses import (
    build_action_summary,
    build_error_response,
    build_success_response,
    json_response,
    truncate_with_file_fallback,
)

__all__ = [
    "DEFAULT_MAX_CHARS",
    "TEMP_DIR",
    "atomic_write_text",
    "build_action_summary",
    "build_error_response",
    "build_success_response",
    "format_size",
    "json_response",
    "normalize_path",
    "save_to_temp_file",
    "truncate_with_file_fallback",
    "validate_path",
]
