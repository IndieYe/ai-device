"""One-shot acceptance. Only processes created here in this isolated container are stopped."""

import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from device_runtime.http_client import DeviceAPI, DeviceAPIError
from device_runtime.installation import install_instance, rebuild_instance
from device_runtime.lifecycle import (
    activate_release,
    backup_instance,
    prepare_upgrade,
    restore_instance,
    rollback_release,
)

WORK = Path("/work")
ROOT = WORK / "instance"
E = WORK / "evidence"
E.mkdir(exist_ok=True)
MARKER = WORK / "started.json"
assert not MARKER.exists(), "One-shot run already started: inspect existing receipts, never rerun"
MARKER.write_text(json.dumps({"started": time.time(), "pid": os.getpid()}))
assert os.getuid() != 0, "Client acceptance must run as a non-root user"
DEVICE = "ubuntu-arm64-test"
URL = "http://127.0.0.1:28766"
ADMIN, TOKEN = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
REPORT = {
    "status": "running",
    "uid": os.getuid(),
    "platform": sys.platform,
    "python": sys.version.split()[0],
    "entry": "independent stdio MCP -> temporary container loopback center -> installed Linux client",
    "not_tested": ["Mac main ServerPool", "public network", "systemd", "x86_64"],
}


def save():
    (E / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2))


save()
install = install_instance(
    Path("/delivery/linux-bundle"), instance_id=DEVICE, roles=["client"], root=ROOT, server_url=URL
)
REPORT["install"] = install
save()
active = json.loads((ROOT / "active.json").read_text())
client_python = ROOT / "releases" / active["release"] / "client/venv/bin/python"
verify = r"""
import hashlib,json,zipfile
from pathlib import Path
from importlib.metadata import distribution
bundle=Path('/delivery/linux-bundle');m=json.loads((bundle/'manifest.json').read_text());counts={}
for c,e in m['components'].items():
 d=distribution(e['distribution']);n=0
 with zipfile.ZipFile(bundle/e['path']) as z:
  for name in z.namelist():
   if name.endswith('.py') and '.dist-info/' not in name:
    assert d.locate_file(name).read_bytes()==z.read(name),(c,name)
    n+=1
 counts[c]=n
print(json.dumps(counts))
"""
REPORT["installed_module_byte_counts"] = json.loads(
    subprocess.check_output([str(client_python), "-I", "-c", verify], text=True)
)
save()
operator = WORK / "operator"
operator.mkdir(mode=0o700)
launch = operator / "launch.py"
launch.write_text(
    "#!"
    + sys.executable
    + '\nimport os,sys\nos.execv(sys.executable,[sys.executable,"-I","-m","device_runtime.mcp_server"])\n'
)
launch.chmod(0o700)
center_env = os.environ.copy()
center_env.update(
    DEVICE_RUNTIME_ADMIN_TOKEN=ADMIN, DEVICE_RUNTIME_DEVICE_TOKENS=json.dumps({DEVICE: TOKEN})
)
client_env = os.environ.copy()
client_env["DEVICE_RUNTIME_TOKEN"] = TOKEN
mcp_env = os.environ.copy()
mcp_env.update(DEVICE_RUNTIME_API_URL=URL, DEVICE_RUNTIME_ADMIN_TOKEN=ADMIN)
processes = []
logs = []


def start_pair(root):
    for name, argv, env in [
        (
            "center",
            [
                sys.executable,
                "-I",
                "-m",
                "device_runtime.cli",
                "server",
                "--state-dir",
                str(WORK / "center"),
                "--port",
                "28766",
            ],
            center_env,
        ),
        ("client", [str(root / "run"), "client"], client_env),
    ]:
        log = (E / (name + "-" + root.name + ".log")).open("w")
        logs.append(log)
        processes.append(subprocess.Popen(argv, env=env, stdout=log, stderr=log))


async def wait_online():
    for _ in range(200):
        if any(p.poll() is not None for p in processes):
            raise RuntimeError("Isolated test process exited; inspect retained logs")
        try:
            async with DeviceAPI(URL, ADMIN) as api:
                devices = await api.list_devices()
            if any(d["device_id"] == DEVICE and d["online"] for d in devices["devices"]):
                return
        except DeviceAPIError:
            pass
        await asyncio.sleep(0.05)
    raise RuntimeError("Isolated client did not become online")


def stop_pair():
    # These handles belong ONLY to new disposable test processes above, never real services.
    for p in reversed(processes):
        if p.poll() is None:
            p.terminate()
        p.wait(timeout=15)
    processes.clear()
    for log in logs:
        log.close()
    logs.clear()


def persistent(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for name in ["instance.json", "run", "config", "state"]
        for p in ([root / name] if (root / name).is_file() else (root / name).rglob("*"))
        if p.is_file()
    }


def check_db(path, table, expected):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        states = dict(db.execute(f"SELECT state,count(*) FROM {table} GROUP BY state"))
        assert states == {"succeeded": expected}, states
    return states


try:
    start_pair(ROOT)
    asyncio.run(wait_online())
    subprocess.run([sys.executable, "-I", "/input/live-acceptance.py"], env=mcp_env, check=True)
    stop_pair()
    live = json.loads((E / "live-acceptance-report.json").read_text())
    n = live["accepted_operation_count"]
    REPORT["live"] = {
        "status": live["status"],
        "tools": 11,
        "accepted_operations": n,
        "checks": live["checks"],
    }
    REPORT["journals"] = {
        "center": check_db(WORK / "center/operations.sqlite3", "operations", n),
        "client": check_db(ROOT / "state/client/operations.sqlite3", "operations", n),
        "tasks": check_db(ROOT / "state/client/tasks.sqlite3", "operations", 1),
    }
    before = persistent(ROOT)
    rebuilt = rebuild_instance(ROOT)
    assert persistent(ROOT) == before
    candidate = prepare_upgrade(ROOT, Path("/delivery/linux-upgrade-bundle"), stopped=True)
    activate_release(ROOT, candidate["release"], stopped=True)
    assert persistent(ROOT) == before
    rollback_release(ROOT, rebuilt["release"], stopped=True)
    assert persistent(ROOT) == before
    backup_instance(ROOT, WORK / "backup", stopped=True)
    restore_instance(WORK / "backup", WORK / "restored")
    assert persistent(WORK / "restored") == before
    start_pair(WORK / "restored")
    asyncio.run(wait_online())
    stop_pair()
    REPORT["restored_journals"] = {
        "center": check_db(WORK / "center/operations.sqlite3", "operations", n),
        "client": check_db(WORK / "restored/state/client/operations.sqlite3", "operations", n),
        "tasks": check_db(WORK / "restored/state/client/tasks.sqlite3", "operations", 1),
    }
    REPORT["lifecycle"] = {
        "rebuild": "PASS",
        "upgrade": "PASS",
        "rollback": "PASS",
        "backup_restore": "PASS",
        "restored_client_online": "PASS",
        "persistent_sha256": before,
    }
    REPORT["status"] = "PASS"
    save()
    print(
        json.dumps(
            {
                "status": "PASS",
                "uid": os.getuid(),
                "operations": n,
                "modules": REPORT["installed_module_byte_counts"],
                "lifecycle": "PASS",
            }
        )
    )
except BaseException as exc:
    REPORT["status"] = "FAILED_NEEDS_RECONCILIATION"
    REPORT["failure_type"] = type(exc).__name__
    save()
    raise
finally:
    if processes:
        stop_pair()
