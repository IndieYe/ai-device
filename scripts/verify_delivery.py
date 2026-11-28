"""Verify minimal extras and end-to-end center/client/mcp wiring from built wheels.

Never uses real devices, credentials, or service managers. Only spawned test processes
are terminated.
"""

import hashlib
import json
import os
import platform
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ROLE_EXTRAS = {
    "ops": ("ops", None),
    "client": ("client", "from device_runtime.executor import LocalExecutor"),
    "center": ("server", "from device_runtime.server import create_app"),
    "mcp": ("mcp", "from device_runtime.mcp_server import create_mcp"),
}


def cleanup_process(process, grace=5):
    """Reap only our start_new_session process group, including descendants."""
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
        # Parent may exit before its children; still sweep this owned group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    process.wait(timeout=grace)


def execute(argv, cwd, env, timeout=180):
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as original:
        try:
            cleanup_process(process)
        except (OSError, subprocess.SubprocessError) as cleanup_error:
            original.add_note(f"Owned process cleanup failed: {cleanup_error!r}")
        raise
    if process.returncode:
        raise RuntimeError(
            f"Command failed ({process.returncode}): {argv[:4]}\n"
            f"stdout (last 12000 characters):\n{stdout[-12000:]}\n"
            f"stderr (last 3000 characters):\n{stderr[-3000:]}"
        )
    return stdout


def _install_role(work, wheel, dist_dir, role, env):
    extra, import_check = ROLE_EXTRAS[role]
    venv = work / f"venv-{role}"
    execute(["uv", "venv", "--python", "3.13", str(venv)], work, env)
    python = venv / "bin/python"
    execute(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--find-links",
            str(dist_dir),
            f"{wheel}[{extra}]",
        ],
        work,
        env,
    )
    snippet = "import importlib.metadata as m,json; "
    if import_check:
        snippet += import_check + "; "
    snippet += "print(json.dumps({d.metadata['Name']:d.version for d in m.distributions()}))"
    installed = json.loads(execute([str(python), "-I", "-c", snippet], work, env))
    if role == "ops":
        assert not ({"ai-device-bash", "ai-device-fs", "fastapi", "mcp"} & installed.keys())
    else:
        assert ("ai-device-bash" in installed) == (role == "client")
        assert ("ai-device-fs" in installed) == (role == "client")
    return venv, installed


def main():
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEVICE_", "MCP_", "PYTHONPATH", "VIRTUAL_ENV"))
    }
    dist_dir = ROOT / "dist"
    wheel = next(dist_dir.glob("ai_device-*.whl"))
    with tempfile.TemporaryDirectory(prefix="ai-device-delivery-") as tmp:
        work = Path(tmp)
        commit = execute(["git", "rev-parse", "HEAD"], ROOT, env).strip()
        report = {"minimal_roles": {}, "lifecycle": {}}
        venvs = {}
        for role in ROLE_EXTRAS:
            venv, installed = _install_role(work, wheel, dist_dir, role, env)
            venvs[role] = venv
            report["minimal_roles"][role] = installed

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        admin_token = "test-only-admin-token-not-secret"
        device_token = "test-only-device-token-not-secret"
        center_env = {
            **env,
            "DEVICE_RUNTIME_ADMIN_TOKEN": admin_token,
            "DEVICE_RUNTIME_DEVICE_TOKENS": json.dumps({"acceptance-only": device_token}),
        }
        center_state = work / "state-center"
        client_state = work / "state-client"
        center_state.mkdir()
        client_state.mkdir()

        center = subprocess.Popen(
            [
                str(venvs["center"] / "bin/device-runtime"),
                "server",
                "--state-dir",
                str(center_state),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=work,
            env=center_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            devices_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/devices",
                headers={"Authorization": "Bearer " + admin_token},
            )
            for _ in range(100):
                assert center.poll() is None, "temporary center exited"
                try:
                    with urllib.request.urlopen(devices_req, timeout=0.2) as response:
                        assert response.status == 200
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError("temporary center unavailable")
            report["lifecycle"]["center_http"] = True

            client_env = {**env, "DEVICE_RUNTIME_TOKEN": device_token}
            client = subprocess.Popen(
                [
                    str(venvs["client"] / "bin/device-runtime"),
                    "client",
                    "--server-url",
                    f"ws://127.0.0.1:{port}",
                    "--device-id",
                    "acceptance-only",
                    "--state-dir",
                    str(client_state),
                ],
                cwd=work,
                env=client_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                for _ in range(100):
                    assert client.poll() is None, "temporary client exited"
                    with urllib.request.urlopen(devices_req, timeout=1) as response:
                        devices = json.load(response)
                    if "acceptance-only" in json.dumps(devices):
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError("temporary client not connected")
                report["lifecycle"]["client_connected"] = True

                mcp_python = venvs["mcp"] / "bin/python"
                mcp_bin = venvs["mcp"] / "bin/device-mcp"
                probe = work / "mcp_probe.py"
                probe.write_text(
                    """import asyncio, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    parameters = StdioServerParameters(command=sys.argv[1], args=[], env=dict(os.environ))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 12
            result = await session.call_tool("list_devices", {})
            assert "acceptance-only" in str(result)
            print("mcp-device-list-ok")
asyncio.run(main())
"""
                )
                mcp_env = {
                    **env,
                    "DEVICE_RUNTIME_API_URL": f"http://127.0.0.1:{port}",
                    "DEVICE_RUNTIME_ADMIN_TOKEN": admin_token,
                }
                output = execute([str(mcp_python), "-I", str(probe), str(mcp_bin)], work, mcp_env)
                assert "mcp-device-list-ok" in output
                report["lifecycle"]["mcp_list_devices"] = True
            finally:
                cleanup_process(client)
        finally:
            cleanup_process(center)

        report.update(
            {
                "platform": platform.platform(),
                "source_commit": commit,
                "note": "Working tree and artifact hashes must also be captured; commit alone is not full identity.",
                "artifacts": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in dist_dir.iterdir()
                    if p.suffix in (".whl", ".gz")
                },
            }
        )
        (ROOT / "artifacts").mkdir(exist_ok=True)
        (ROOT / "artifacts/delivery-verification.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        print(json.dumps(report["lifecycle"], indent=2))


if __name__ == "__main__":
    main()
