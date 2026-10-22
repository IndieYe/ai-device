"""Formatting helpers."""

from __future__ import annotations


def format_size(bytes_size: int) -> str:
    """Format a byte count into a human-readable string."""
    units = ["B", "KB", "MB", "GB", "TB"]
    if bytes_size == 0:
        return "0 B"

    size = float(bytes_size)
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    return f"{size:.2f} {units[unit_index]}"
