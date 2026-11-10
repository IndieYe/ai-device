"""Regression for cancellation of a synchronous filesystem tool over stdio."""

import asyncio
import json
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["write_file", "read_file"])
async def test_cancelled_file_call_keeps_connection_usable(tmp_path, operation):
    marker = tmp_path / "started"
    finished = tmp_path / "finished"
    release = tmp_path / "release"
    target = tmp_path / "result.txt"
    server = tmp_path / "isolated_server.py"
    if operation == "read_file":
        target.write_text("original", encoding="utf-8")
    server.write_text(
        "import time\n"
        "from pathlib import Path\n"
        "from mcp_server_fs import core\n"
        "from mcp_server_fs.server import create_mcp\n"
        f"original = core.{operation}\n"
        "def delayed(*args, **kwargs):\n"
        f"    Path({str(marker)!r}).write_text('started')\n"
        f"    release = Path({str(release)!r})\n"
        "    deadline = time.monotonic() + 8\n"
        "    while not release.exists() and time.monotonic() < deadline:\n"
        "        time.sleep(0.01)\n"
        "    result = original(*args, **kwargs)\n"
        f"    Path({str(finished)!r}).write_text('finished')\n"
        "    return result\n"
        f"core.{operation} = delayed\n"
        "mcp, _ = create_mcp()\n"
        "mcp.run(transport='stdio')\n",
        encoding="utf-8",
    )
    params = StdioServerParameters(command=sys.executable, args=[str(server)], cwd=str(tmp_path))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        # SDK 1.30 assigns this counter synchronously before the first await.
        request_id = session._request_id
        arguments = {"path": str(target)}
        if operation == "write_file":
            arguments["content"] = "written"
        pending = asyncio.create_task(session.call_tool(operation, arguments))
        try:
            with anyio.fail_after(8):
                while not marker.exists():
                    await asyncio.sleep(0.02)
            await session.send_notification(types.ClientNotification(types.CancelledNotification(
                params=types.CancelledNotificationParams(requestId=request_id, reason="test cancellation")
            )))
            with pytest.raises(Exception, match="cancel"):
                await asyncio.wait_for(pending, 3)
            if operation == "write_file":
                assert not target.exists()
            # The abandoned worker is still blocked; other tools must not wait
            # for its file operation to finish.
            if operation == "write_file":
                healthy = await asyncio.wait_for(session.call_tool("read_file", {"path": str(marker)}), 3)
            else:
                healthy = await asyncio.wait_for(session.call_tool(
                    "write_file", {"path": str(tmp_path / "other.txt"), "content": "other"}
                ), 3)
            assert not healthy.isError
            release.write_text("release", encoding="utf-8")
            with anyio.fail_after(8):
                while not finished.exists():
                    await asyncio.sleep(0.02)
            expected = "written" if operation == "write_file" else "original"
            assert target.read_text(encoding="utf-8") == expected
            # Give the completed worker time to expose a duplicate-response race.
            await asyncio.sleep(0.1)
            result = await asyncio.wait_for(session.call_tool("read_file", {"path": str(target)}), 3)
            assert not result.isError
            assert json.loads(result.content[0].text)["content"] == expected
        finally:
            release.touch(exist_ok=True)
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
