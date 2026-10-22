"""Real MCP protocol checks in a fresh process, independent of unit-test stubs."""

import json
import os
from pathlib import Path
import subprocess
import sys


PROBE = r'''
import asyncio
import inspect
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch

from fastmcp import Client
from mcp_server_bash import server

root = Path(sys.argv[1])
server.BASH_OUTPUT_DIR = str(root / "outputs")
server.BASH_ARTIFACT_DIR = str(root / "artifacts")
server.DEFAULT_CWD = str(root)


def payload(result):
    assert result.is_error is False, result.content
    return json.loads(result.content[0].text)


async def probe():
    checked = []
    async with Client(server.mcp) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == {"bash", "run", "script", "get"}
        for tool in tools:
            assert "host" not in tool.inputSchema["properties"], tool.name
            assert "host" not in inspect.signature(getattr(server, tool.name)).parameters

        marker = root / "must-not-exist"
        cases = {
            "bash": ({"command": f"touch {marker}"}, "run_command"),
            "run": ({"argv": [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)]}, "run_argv"),
            "script": ({"script": f"touch {marker}\n"}, "run_script"),
            "get": ({"pid": 123}, "get_background_result"),
        }
        for name, (arguments, executor) in cases.items():
            for host in ["prod-1", "", None]:
                with patch.object(server, executor, new_callable=AsyncMock) as operation:
                    result = await client.call_tool(name, {**arguments, "host": host}, raise_on_error=False)
                assert result.is_error is True, (name, host, result)
                assert "host" in result.content[0].text
                operation.assert_not_called()
                assert not marker.exists()
                assert not (root / "outputs").exists()
                assert not (root / "artifacts").exists()
                checked.append([name, host])

        shell = payload(await client.call_tool("bash", {"command": "printf local-bash"}))
        assert shell["code"] == "OK" and shell["output"] == "local-bash"
        run = payload(await client.call_tool("run", {"argv": [sys.executable, "-c", "import sys; print(sys.stdin.read())"], "stdin": "local-stdin"}))
        assert run["code"] == "OK" and run["output"].strip() == "local-stdin"
        script = payload(await client.call_tool("script", {"script": "printf local-script\n"}))
        assert script["code"] == "OK" and script["output"] == "local-script"
        assert not list((root / "artifacts").iterdir())

        background = payload(await client.call_tool("run", {"argv": [sys.executable, "-c", "import time; time.sleep(0.15); print('local-background')"], "timeout": 0.01}))
        assert background["code"] == "BACKGROUND"
        finished = payload(await client.call_tool("get", {"pid": background["background_pid"], "timeout": 2, "bytes": 1000}))
        assert finished["code"] == "OK" and finished["output"].strip() == "local-background"
        repeated = payload(await client.call_tool("get", {"pid": background["background_pid"], "bytes": 1000}))
        assert repeated["code"] == "OK" and repeated["output"] == finished["output"]

    # Lifespan owns the runtime, including termination of its pending commands.
    import psutil
    previous_runtime = server.RUNTIME
    assert previous_runtime._closed
    async with Client(server.mcp) as client:
        assert server.RUNTIME is not previous_runtime
        pending = payload(await client.call_tool("script", {
            "script": "sleep 30\n", "stdin": "owned-input", "timeout": 0.01,
        }))
        assert pending["code"] == "BACKGROUND"
        pid = pending["background_pid"]
        assert psutil.pid_exists(pid)
    assert not psutil.pid_exists(pid)
    assert not server.RUNTIME._tracker.processes
    assert not list((root / "artifacts").iterdir())

    print(json.dumps({"host_rejections": checked, "local_tools": 4, "background_get": "passed"}))


asyncio.run(probe())
'''


def test_real_mcp_local_only_contract(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repository / "src")
    for name in list(env):
        if name.startswith("AI_ASSISTANT_"):
            env.pop(name)
    completed = subprocess.run(
        [sys.executable, "-c", PROBE, str(tmp_path)],
        cwd=repository,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert len(result["host_rejections"]) == 12
    assert result["local_tools"] == 4
    assert result["background_get"] == "passed"
