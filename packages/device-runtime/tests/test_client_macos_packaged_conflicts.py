"""Current conflict source on an explicit interpreter, with host enumeration replaced.

Set AI_DEVICE_TEST_PACKAGED_PYTHON to test a retained full-tree interpreter.
No host discovery, service actions or changes to that tree occur.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("case", ["clear", "client", "unknown"])
def test_current_conflicts_with_packaged_dependencies(case):
    interpreter = os.environ.get("AI_DEVICE_TEST_PACKAGED_PYTHON", sys.executable)
    module = Path(__file__).parents[1] / "src/device_runtime/client_macos_conflicts.py"
    code = r"""
import importlib.util, sys, pwd, psutil
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location("offline_conflicts",sys.argv[1])
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
case=sys.argv[2]
# Replace every host enumeration boundary before entering the actual main.
pwd.getpwall=lambda: [SimpleNamespace(pw_dir="/offline-test-home")]
calls=[]
def files(homes, *, service_directories, own_plist=None):
    assert list(homes)==[m.Path("/offline-test-home")]
    assert service_directories==[m.Path("/Library/LaunchAgents"),m.Path("/Library/LaunchDaemons")]
    calls.append("files")
    if case=="unknown": raise OSError("sensitive-home-and-command")
m.Path=__import__("pathlib").Path
m.check_known_files=files
class Process:
    def cmdline(self):
        return [m.FIXED_LAUNCHER] if case=="client" else ["/usr/bin/true"]
def processes():
    calls.append("processes")
    return [Process()]
psutil.process_iter=processes
m.sys.platform="darwin"
m.os.geteuid=lambda: 0
status=m.main(["--confirm-legacy-clients-disabled"])
assert calls==(["files"] if case=="unknown" else ["files","processes"])
raise SystemExit(status)
"""
    result = subprocess.run(
        [interpreter, "-I", "-B", "-c", code, str(module), case],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
    )
    assert result.returncode == (0 if case == "clear" else 70), result.stderr
    assert result.stdout == ("AI_DEVICE_HOST_CONFLICTS_CLEAR_V1\n" if case == "clear" else "")
    assert result.stderr == ("" if case == "clear" else "Mac host conflict check refused\n")
