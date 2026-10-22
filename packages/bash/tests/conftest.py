from __future__ import annotations

import sys
from pathlib import Path

import pytest_asyncio

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))



@pytest_asyncio.fixture(autouse=True)
async def runtime(tmp_path, monkeypatch):
    """A fresh execution owner, shared with the MCP adapter when it is loaded."""
    from mcp_server_bash.core import ExecutionRuntime

    instance = ExecutionRuntime(
        output_dir=str(tmp_path / "outputs"), artifact_dir=str(tmp_path / "artifacts")
    )
    server = sys.modules.get("mcp_server_bash.server")
    if server is not None:
        monkeypatch.setattr(server, "RUNTIME", instance)
    try:
        yield instance
    finally:
        await instance.aclose()
