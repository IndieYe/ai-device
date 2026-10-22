"""One-shot real offline lifecycle checks in a fresh, explicitly isolated container.

No center connection, Token, service manager or host data is used. This validates
software preparation, not websocket transport or systemd/reboot recovery.
"""

import json
import platform
import subprocess
import sys
from pathlib import Path

from device_runtime.bundle import inspect_bundle
from device_runtime.installation import install_instance, rebuild_instance
from device_runtime.lifecycle import activate_release, prepare_upgrade, rollback_release


def check_images(python):
    code = """
from io import BytesIO
from PIL import Image
from mcp_server_fs.image_utils import validate_image_bytes
for fmt in ('PNG', 'JPEG', 'WEBP'):
    stream = BytesIO()
    Image.new('RGB', (32, 24), (12, 34, 56)).save(stream, format=fmt)
    validate_image_bytes(stream.getvalue(), path='acceptance.' + fmt.lower(), max_bytes=5*1024*1024)
    print(fmt, 'FS validation OK')
try:
    validate_image_bytes(b'invalid', path='invalid.png', max_bytes=1024)
except Exception as exc:
    assert 'IMAGE_INVALID' in str(exc) or getattr(exc, 'code', None) == 'IMAGE_INVALID', repr(exc)
else:
    raise AssertionError('invalid image accepted')
import device_runtime.client, mcp_server_bash.core, psutil, pydantic, websockets
"""
    subprocess.run([str(python), "-I", "-c", code], check=True)


def main(bundle, workspace):
    assert platform.system() == "Linux" and platform.machine() == "x86_64"
    assert platform.libc_ver()[0] == "glibc"
    assert sys.version_info[:2] == (3, 13)
    workspace.mkdir(exist_ok=False)
    (workspace / "started.json").write_text(
        json.dumps({"bundle": str(bundle), "python": sys.version})
    )
    manifest = inspect_bundle(bundle)
    assert manifest["format_version"] == 3
    root = workspace / "instance"
    initial = install_instance(
        bundle,
        root=root,
        instance_id="centos7-isolated",
        roles=["client"],
        server_url="https://isolated.invalid",
    )
    check_images(root / "releases" / initial["release"] / "client/venv/bin/python")
    rebuilt = rebuild_instance(root)
    active = json.loads((root / "active.json").read_text())
    check_images(root / "releases" / active["release"] / "client/venv/bin/python")
    candidate = prepare_upgrade(root, bundle, stopped=True)
    activate_release(root, candidate["release"], stopped=True)
    check_images(root / "releases" / candidate["release"] / "client/venv/bin/python")
    rollback_release(root, active["release"], stopped=True)
    assert json.loads((root / "active.json").read_text())["release"] == active["release"]
    # Tampering is detected before replacing the active pointer.
    snapshot = root / "releases" / active["release"] / "bundle"
    wheel = next((snapshot / "dependencies").glob("*.whl"))
    wheel.write_bytes(b"intentional isolated corruption")
    before = (root / "active.json").read_bytes()
    try:
        rebuild_instance(root)
    except ValueError:
        pass
    else:
        raise AssertionError("corrupt dependency snapshot accepted")
    assert (root / "active.json").read_bytes() == before
    report = {
        "status": "passed",
        "platform": platform.platform(),
        "libc": platform.libc_ver(),
        "python": sys.version,
        "initial": initial["release"],
        "rebuild": rebuilt,
        "candidate": candidate["release"],
        "checks": [
            "install",
            "rebuild",
            "prepare",
            "activate",
            "rollback",
            "images",
            "tamper rejection",
        ],
        "limits": "no transport, systemd, reboot or production validation",
    }
    (workspace / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
