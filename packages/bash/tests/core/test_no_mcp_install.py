"""Install the default dependency set and execute from an isolated interpreter."""

import json
import os
import subprocess
from pathlib import Path

PROBE = r'''
import asyncio
import importlib.util
import json
from pathlib import Path
import sys

assert importlib.util.find_spec("fastmcp") is None
assert importlib.util.find_spec("mcp") is None
assert importlib.util.find_spec("pydantic") is None
from mcp_server_bash.core import ExecutionRuntime, create_script_artifacts, create_stdin_artifact

root = Path(sys.argv[1])

async def probe():
    async with ExecutionRuntime(output_dir=str(root / "outputs"), artifact_dir=str(root / "artifacts")) as runtime:
        bash = await runtime.run_command("printf core-shell", cwd=str(root))
        assert bash["code"] == "OK" and bash["output"] == "core-shell"
        stdin_path, artifacts = create_stdin_artifact(b"core-stdin", runtime.artifact_dir)
        run = await runtime.run_argv(
            (sys.executable, "-c", "import sys; print(sys.stdin.read())"),
            cwd=str(root), stdin_path=stdin_path, artifacts=artifacts,
        )
        assert run["code"] == "OK" and run["output"].strip() == "core-stdin"
        path, stdin_path, artifacts = create_script_artifacts(b"cat\n", b"script-stdin", runtime.artifact_dir)
        script = await runtime.run_script(path, cwd=str(root), stdin_path=stdin_path, artifacts=artifacts)
        assert script["code"] == "OK" and script["output"] == "script-stdin"
        background = await runtime.run_argv(
            (sys.executable, "-c", "import time; time.sleep(0.15); print('core-background')"),
            cwd=str(root), timeout=0.01,
        )
        assert background["code"] == "BACKGROUND"
        result = await runtime.get_background_result(background["background_pid"], timeout=2)
        assert result["code"] == "OK" and result["output"].strip() == "core-background"
        assert not list((root / "artifacts").iterdir())
    assert "fastmcp" not in sys.modules and "mcp_server_bash.server" not in sys.modules
    print(json.dumps({"without_mcp": True, "local_modes": 3, "background_get": "passed"}))

asyncio.run(probe())
'''


def test_core_executes_in_install_without_mcp(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    environment = tmp_path / "core-env"
    env = dict(os.environ)
    for name in list(env):
        if name.startswith("AI_ASSISTANT_") or name in {"PYTHONPATH", "VIRTUAL_ENV"}:
            env.pop(name)
    env.update({
        "UV_PROJECT_ENVIRONMENT": str(environment),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "TMPDIR": str(tmp_path),
    })
    installed = subprocess.run(
        ["uv", "sync", "--locked", "--no-default-groups", "--no-dev"],
        cwd=repository, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    completed = subprocess.run(
        [str(environment / "bin" / "python"), "-I", "-c", PROBE, str(tmp_path)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "without_mcp": True, "local_modes": 3, "background_get": "passed",
    }
    cli = subprocess.run(
        [str(environment / "bin" / "python"), "-I", "-m", "mcp_server_bash"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10, check=False,
    )
    assert cli.returncode != 0
    assert "uv sync --extra mcp" in cli.stderr
    assert not (tmp_path / "logs").exists()
