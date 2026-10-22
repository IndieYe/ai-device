"""Verify minimal extras and real macOS lifecycle in temporary installed environments.

Never uses real devices, credentials, or service managers. Only spawned test processes
are terminated. Linux bundle rejects center/mcp; its actual installation is not claimed.
"""

import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import tempfile
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {"ai-device", "ai-device-bash", "ai-device-fs"}


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
            f"Command failed ({process.returncode}): {argv[:4]}\n{stderr[-3000:]}"
        )
    return stdout


def child(work, wheels, pins):
    # Executed by an installed wheel environment, never by the checkout interpreter.
    import socket
    import time
    import urllib.request

    from device_runtime.bundle import BundleError, build_bundle, inspect_bundle
    from device_runtime.installation import (
        InstallationError,
        install_instance,
        rebuild_instance,
    )
    from device_runtime.lifecycle import (
        activate_release,
        backup_instance,
        inspect_instance,
        prepare_upgrade,
        restore_instance,
        rollback_release,
    )

    work, wheels, pins = Path(work), Path(wheels), Path(pins)
    components = {
        "device": next(wheels.glob("ai_device-*.whl")),
        "bash": next(wheels.glob("ai_device_bash-*.whl")),
        "fs": next(wheels.glob("ai_device_fs-*.whl")),
    }
    report = {"minimal_roles": {}, "lifecycle": {}, "third_party": []}
    sources = json.loads((work / "sources.json").read_text())
    bundle = work / "bundle"
    build_bundle(
        bundle,
        release_version="0.2.2",
        wheels=components,
        constraints=pins,
        sources=sources,
        target_system="darwin",
    )
    inspect_bundle(bundle)
    root = work / "instance"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    active = install_instance(
        bundle,
        instance_id="acceptance-only",
        roles=["client", "center", "mcp"],
        root=root,
        server_url=f"http://127.0.0.1:{port}",
        port=port,
    )
    diagnostic = inspect_instance(root)
    assert (
        diagnostic["installation"] == "complete"
        and diagnostic["environments"] == "available"
    ), diagnostic
    report["lifecycle"]["install_and_inspect"] = True
    for role in ("client", "center", "mcp"):
        python = root / "releases" / active["release"] / role / "venv/bin/python"
        modules = {
            "client": "from device_runtime.executor import LocalExecutor",
            "center": "from device_runtime.server import create_app",
            "mcp": "from device_runtime.mcp_server import create_mcp",
        }
        code = (
            "import importlib.metadata as m,json; "
            + modules[role]
            + "; print(json.dumps({d.metadata['Name']:d.version for d in m.distributions()}))"
        )
        packages = json.loads(
            subprocess.check_output(
                [str(python), "-I", "-c", code], cwd=work, text=True
            )
        )
        assert ("ai-device-bash" in packages) == (role == "client")
        assert ("ai-device-fs" in packages) == (role == "client")
        report["minimal_roles"][role] = packages
    env = dict(os.environ)
    env["DEVICE_RUNTIME_ADMIN_TOKEN"] = "test-only-admin-token-not-secret"
    env["DEVICE_RUNTIME_DEVICE_TOKENS"] = json.dumps(
        {"acceptance-only": "test-only-device-token-not-secret"}
    )
    process = subprocess.Popen(
        [str(root / "run"), "center"],
        cwd=work,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        for _ in range(100):
            assert process.poll() is None, "temporary center exited"
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/devices",
                    headers={
                        "Authorization": "Bearer " + env["DEVICE_RUNTIME_ADMIN_TOKEN"]
                    },
                )
                with urllib.request.urlopen(request, timeout=0.2) as response:
                    assert response.status == 200
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("temporary stable-runner center unavailable")
        report["lifecycle"]["stable_runner_center_http"] = True
        client_env = dict(env)
        client_env["DEVICE_RUNTIME_TOKEN"] = "test-only-device-token-not-secret"
        client = subprocess.Popen(
            [str(root / "run"), "client"],
            cwd=work,
            env=client_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            for _ in range(100):
                assert client.poll() is None, "temporary client exited"
                with urllib.request.urlopen(request, timeout=1) as response:
                    devices = json.load(response)
                if "acceptance-only" in json.dumps(devices):
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("temporary client not connected")
            mcp_python = root / "releases" / active["release"] / "mcp/venv/bin/python"
            probe = work / "mcp_probe.py"
            probe.write_text("""import asyncio, json, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    parameters = StdioServerParameters(command=sys.argv[1], args=["mcp"], env=dict(os.environ))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 12
            result = await session.call_tool("list_devices", {})
            assert "acceptance-only" in str(result)
            print("stable-mcp-device-list-ok")
asyncio.run(main())
""")
            output = execute(
                [str(mcp_python), "-I", str(probe), str(root / "run")], work, env
            )
            assert "stable-mcp-device-list-ok" in output
            report["lifecycle"]["stable_runner_client_and_mcp"] = True
        finally:
            cleanup_process(client)
        before = (root / "active.json").read_bytes()
        try:
            prepare_upgrade(root, bundle, stopped=True)
        except (InstallationError, BlockingIOError):
            pass
        else:
            raise AssertionError("prepare permitted while runner holds lease")
        assert (root / "active.json").read_bytes() == before
        report["lifecycle"]["running_lease_rejected"] = True
    finally:
        cleanup_process(process)
    before = (root / "active.json").read_bytes()
    candidate = prepare_upgrade(root, bundle, stopped=True)
    assert (root / "active.json").read_bytes() == before
    activate_release(root, candidate["release"], stopped=True)
    assert (
        json.loads((root / "active.json").read_text())["release"]
        == candidate["release"]
    )
    rebuild_instance(root)
    assert inspect_instance(root)["environments"] == "available"
    report["lifecycle"]["prepare_activate_rebuild"] = True
    rollback_release(root, active["release"], stopped=True)
    assert (
        json.loads((root / "active.json").read_text())["release"] == active["release"]
    )
    backup = work / "backup"
    backup_instance(root, backup, stopped=True)
    restored = work / "restored"
    restore_instance(backup, restored)
    assert inspect_instance(restored)["environments"] == "available"
    report["lifecycle"]["rollback_backup_restore"] = True
    before = (root / "active.json").read_bytes()
    try:
        activate_release(root, "nonexistent-release", stopped=True)
    except InstallationError:
        pass
    else:
        raise AssertionError("invalid release accepted")
    assert (root / "active.json").read_bytes() == before
    report["lifecycle"]["invalid_release_keeps_active"] = True
    linux = work / "linux-client-bundle"
    build_bundle(
        linux,
        release_version="0.2.2",
        wheels=components,
        constraints=pins,
        sources=sources,
        target_system="linux",
    )
    assert set(inspect_bundle(linux)["roles"]) == {"client"}
    try:
        install_instance(
            linux,
            instance_id="reject-target",
            roles=["center"],
            root=work / "invalid-instance",
        )
    except InstallationError:
        pass
    else:
        raise AssertionError("Linux center bundle accepted")
    report["lifecycle"]["linux_target_role_rejected"] = True
    # Tamper only the temporary bundle and check no mutation of active installation.
    manifest = json.loads((bundle / "manifest.json").read_text())
    entry = manifest["components"]["device"]
    with (bundle / entry["path"]).open("ab") as stream:
        stream.write(b"tampered-test-only")
    try:
        prepare_upgrade(root, bundle, stopped=True)
    except (InstallationError, BundleError):
        pass
    else:
        raise AssertionError("tampered wheel accepted")
    assert (root / "active.json").read_bytes() == before
    report["lifecycle"]["tampered_wheel_keeps_active"] = True
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", nargs=3)
    args = parser.parse_args()
    if args.child:
        report = child(*args.child)
        Path(args.child[0], "result.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        return
    if platform.system() != "Darwin":
        raise SystemExit(
            "Real bundle lifecycle currently verified here only on macOS; not silently skipped"
        )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEVICE_", "MCP_", "PYTHONPATH", "VIRTUAL_ENV"))
    }
    with tempfile.TemporaryDirectory(prefix="ai-device-delivery-") as tmp:
        work = Path(tmp)
        commit = execute(["git", "rev-parse", "HEAD"], ROOT, env).strip()
        (work / "sources.json").write_text(
            json.dumps(dict.fromkeys(("device", "bash", "fs"), commit))
        )
        pins = work / "constraints.txt"
        pins.write_text(
            "\n".join(
                sorted(
                    f"{d.metadata['Name']}=={d.version}"
                    for d in metadata.distributions()
                    if d.metadata["Name"] not in PROJECTS
                )
            )
            + "\n"
        )
        venv = work / "driver"
        execute(["uv", "venv", "--python", "3.13", str(venv)], work, env)
        python = venv / "bin/python"
        wheel = next((ROOT / "dist").glob("ai_device-*.whl"))
        execute(
            ["uv", "pip", "install", "--python", str(python), str(wheel) + "[ops]"],
            work,
            env,
        )
        # Also check ops as a genuinely minimal optional dependency installation.
        installed = json.loads(
            execute(
                [
                    str(python),
                    "-I",
                    "-c",
                    "import importlib.metadata as m,json;print(json.dumps({d.metadata['Name']:d.version for d in m.distributions()}))",
                ],
                work,
                env,
            )
        )
        assert not (
            {"ai-device-bash", "ai-device-fs", "fastapi", "mcp"} & installed.keys()
        )
        script = work / "verify_delivery.py"
        script.write_bytes(Path(__file__).read_bytes())
        execute(
            [
                str(python),
                "-I",
                str(script),
                "--child",
                str(work),
                str(ROOT / "dist"),
                str(pins),
            ],
            work,
            env,
        )
        report = json.loads((work / "result.json").read_text())
        report["minimal_roles"]["ops"] = installed
        report.update(
            {
                "platform": platform.platform(),
                "source_commit": commit,
                "note": "Working tree and artifact hashes must also be captured; commit alone is not full identity.",
                "artifacts": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (ROOT / "dist").iterdir()
                    if p.suffix in (".whl", ".gz")
                },
            }
        )
        (ROOT / "artifacts/delivery-verification.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        print(json.dumps(report["lifecycle"], indent=2))


if __name__ == "__main__":
    main()
