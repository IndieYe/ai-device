"""Backward-compatible root entrypoint.

Prefer importing from `mcp_server_fs.cli` or running `python -m mcp_server_fs`.
This module intentionally keeps a minimal compatibility surface for older tests
and integrations that still import tools from `main`.
"""

from mcp_server_fs.cli import apply_patch, edit_file, insert_file, main, mcp, read_file, read_image, write_file

__all__ = [
    "apply_patch",
    "edit_file",
    "insert_file",
    "main",
    "mcp",
    "read_file",
    "read_image",
    "write_file",
]


if __name__ == "__main__":
    main()
