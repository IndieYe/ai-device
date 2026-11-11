"""Opt-in full-tree diagnostic fixture; source tree remains untouched.

The copied package initializer replaces all discovery before actual -m entry.
This tests real native/Python plumbing, not authentic production discovery.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not os.environ.get("AI_DEVICE_TEST_MAC_TREE"),
    reason="requires explicit retained Mac tree; never host discovery",
)


@pytest.mark.parametrize("case", ["clear", "client", "unknown"])
def test_native_packaged_module(tmp_path, case):
    tree = Path(os.environ["AI_DEVICE_TEST_MAC_TREE"])
    source = tmp_path / "resources/client-tree"
    shutil.copytree(
        tree, source, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    package = source / "runtime/lib/python3.13/site-packages/device_runtime"
    current = Path(__file__).parents[1] / "src/device_runtime/client_macos_conflicts.py"
    shutil.copyfile(current, package / "client_macos_conflicts.py")
    # Replace fixture initializer, NOT production conflict source. Enumerations
    # cannot fall back to the real host even if the expected path list changes.
    (package / "__init__.py").write_text(
        "import os, pwd, psutil\n"
        "pwd.getpwall=lambda: []\n"
        "os.geteuid=lambda: 0\n"
        "class Empty:\n"
        " def __enter__(self): return iter(())\n"
        " def __exit__(self,*args): pass\n"
        "def scan(path):\n"
        ' if str(path) not in ("/Library/LaunchAgents","/Library/LaunchDaemons"): raise OSError("unexpected fixture path")\n'
        + (
            ' raise OSError("private data must not escape")\n'
            if case == "unknown"
            else " return Empty()\n"
        )
        + "os.scandir=scan\n"
        "class Process:\n"
        ' def cmdline(self): return ["/opt/ai-device-client/bin/ai-device-client-launcher"]\n'
        + (
            "psutil.process_iter=lambda: [Process()]\n"
            if case == "client"
            else "psutil.process_iter=lambda: []\n"
        )
    )
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("full_probe_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), tmp_path, build)
    management = tmp_path / "management"
    management.mkdir(mode=0o700)
    (management / "management.lock").touch(mode=0o600)
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "probe.m"
    harness.write_text(
        "#define main offline_writer_main\n#define EMBEDDED_RESOURCE_CHECK\n"
        f'#include "{native / "client-macos-writer-probe.c"}"\n#undef main\n'
        f'#include "{native / "client-macos-bound-resources.h"}"\n'
        f'#include "{native / "client-macos-host-conflicts.h"}"\n'
        'int main(int argc,char **argv) {(void)argc;int fd=acquire_private_lock(argv[1],"management.lock",getuid());\n'
        "if(fd<0)return 2;int clear=check_bound_host_conflicts(argv[1],getuid(),fd,1,10);\n"
        'if(acquire_private_lock(argv[1],"management.lock",getuid())!=-2)return 3;\n'
        "int status;if(waitpid(-1,&status,WNOHANG)!=-1 || errno!=ECHILD)return 4;\n"
        'close(fd);printf("%d\\n",clear);return 0;}\n'
    )
    binary = tmp_path / "probe"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(build),
            str(harness),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [str(binary), str(management)], capture_output=True, text=True, check=True, timeout=15
    )
    assert result.stdout == ("1\n" if case == "clear" else "0\n")
    # This exact module was the inventoried and executed resource.
    assert (package / "client_macos_conflicts.py").read_bytes() == current.read_bytes()


def test_builder_identity_matches_packaged_runtime(tmp_path):
    import json

    source = tmp_path / "full-tree"
    shutil.copytree(
        Path(os.environ["AI_DEVICE_TEST_MAC_TREE"]),
        source,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "client-identity.json"),
    )
    script = Path(__file__).parents[1] / "scripts/build-client-tree.py"
    spec = importlib.util.spec_from_file_location("full_identity_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    expected = builder.write_client_identity(source)
    result = subprocess.run(
        [
            str(source / "runtime/bin/python3"),
            "-I",
            "-B",
            "-c",
            "import json;from device_runtime.version import runtime_version;print(json.dumps(runtime_version()))",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
    )
    assert json.loads(result.stdout) == expected
    assert json.loads((source / "client-identity.json").read_text()) == expected
