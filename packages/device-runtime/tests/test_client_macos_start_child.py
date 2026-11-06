"""Native fixed helper transport only; fake Python executable, no launchctl."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin spawn APIs")


@pytest.mark.parametrize(
    "case",
    ["clear", "extra", "nonzero", "flood", "timeout", "changed", "bad-resource", "bad-owner"],
)
def test_native_start_child_transport(tmp_path, case):
    native = Path(__file__).parents[1] / "native"
    management = tmp_path / "management"
    management.mkdir(mode=0o700)
    (management / "management.lock").touch(mode=0o600)
    source = tmp_path / "source"
    (source / "runtime/bin").mkdir(parents=True)
    child = tmp_path / "child.c"
    child.write_text(
        r"""
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
int main(int argc,char **argv) {
 if(argc!=6 || strcmp(argv[1],"-I") || strcmp(argv[2],"-B") || strcmp(argv[3],"-m") ||
    strcmp(argv[4],"device_runtime.client_macos_start_entry") || strcmp(argv[5],"--inherit-management-lock"))return 80;
 if(fcntl(4,F_GETFD)<0 || (fcntl(4,F_GETFD)&FD_CLOEXEC))return 81;
 int fd=open("MANAGEMENT/management.lock",O_RDWR);
 if(fd<0 || flock(fd,LOCK_EX|LOCK_NB)==0)return 82;
 close(fd);
 if(flock(4,LOCK_EX|LOCK_NB))return 83;
 if(!strcmp("CASE","timeout")) {sleep(2);return 0;}
 if(!strcmp("CASE","flood")) {for(int i=0;i<1000;i++)putchar('x');return 0;}
 if(!strcmp("CASE","changed"))rename("MANAGEMENT/management.lock","MANAGEMENT/retained.lock");
 puts("AI_DEVICE_REGISTERED_START_REQUESTED_V1");
 if(!strcmp("CASE","extra"))puts("extra");
 return !strcmp("CASE","nonzero");
}
""".replace("MANAGEMENT", str(management)).replace("CASE", case)
    )
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(child),
            "-o",
            str(source / "runtime/bin/python3"),
        ],
        check=True,
        capture_output=True,
    )
    harness = tmp_path / "parent.c"
    harness.write_text(
        "#include <string.h>\n"
        f'#include "{native / "client-macos-private-lock.h"}"\n'
        f'#define TEST_TREE "{source}"\n'
        f"static int verify_resource_tree(const char *p) {{(void)p;return {0 if case == 'bad-resource' else 1};}}\n"
        f"static int tree_owner_valid(const char *p,uid_t u,int d) {{(void)p;(void)u;(void)d;return {0 if case == 'bad-owner' else 1};}}\n"
        f'#include "{native / "client-macos-start-child.h"}"\n'
        'int main(int argc,char **argv) {(void)argc;int fd=acquire_private_lock(argv[1],"management.lock",getuid());'
        "if(fd<0)return 1;int result=request_bound_registered_start(argv[1],getuid(),fd,TEST_TREE,"
        + ("0.2" if case == "timeout" else "2")
        + ");\n"
        "int status;if(waitpid(-1,&status,WNOHANG)!=-1 || errno!=ECHILD)return 2;\n"
        'char retained[PATH_MAX];snprintf(retained,sizeof(retained),"%s/%s",argv[1],'
        + ('"retained.lock"' if case == "changed" else '"management.lock"')
        + ");\n"
        "int independent=open(retained,O_RDWR);if(independent<0 || flock(independent,LOCK_EX|LOCK_NB)==0)return 3;"
        'close(independent);close(fd);printf("%d\\n",result);return 0;}\n'
    )
    binary = tmp_path / "parent"
    subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", str(harness), "-o", str(binary)],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [str(binary), str(management)], check=True, capture_output=True, text=True, timeout=4
    )
    assert result.stdout == ("1\n" if case == "clear" else "0\n")


@pytest.mark.skipif(
    not os.environ.get("AI_DEVICE_TEST_MAC_TREE"),
    reason="explicit packaged tree required",
)
@pytest.mark.parametrize("case", ["clear", "nonzero", "changed", "tampered"])
def test_native_start_real_packaged_entry(tmp_path, case):
    """Real Python/entry/held core; transport resource verification is isolated."""
    import importlib.util
    import shutil

    native = Path(__file__).parents[1] / "native"
    management = tmp_path / "management"
    management.mkdir(mode=0o700)
    (management / "management.lock").touch(mode=0o600)
    source = tmp_path / "resources/client-tree"
    shutil.copytree(
        Path(os.environ["AI_DEVICE_TEST_MAC_TREE"]),
        source,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    package = source / "runtime/lib/python3.13/site-packages/device_runtime"
    shutil.copytree(
        Path(__file__).parents[1] / "src/device_runtime",
        package,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    initializer = (package / "__init__.py").read_text()
    (package / "__init__.py").write_text(
        initializer
        + r"""
import fcntl, os, subprocess
from pathlib import Path
from types import SimpleNamespace
from . import client_macos_management as management, client_macos_start as start
from .client_macos_management import management_lock_matches
root=Path(TEST_MANAGEMENT_PATH)
owner=os.getuid()
management.MANAGEMENT_DIRECTORY=root
management.management_lock_matches=lambda fd,path,owner_uid: management_lock_matches(fd,path,owner_uid=owner)
os.getuid=lambda: 0
os.geteuid=lambda: 0
account=SimpleNamespace(layout=SimpleNamespace(uid=501))
start.registered_account=lambda: account
start.validate_agent=lambda account: None
start.observe_service=lambda *args,**kwargs: SimpleNamespace(target="gui/501/local.ai-device.client")
def action(argv,**kwargs):
    assert argv==["/bin/launchctl","kickstart","gui/501/local.ai-device.client"]
    assert not os.get_inheritable(4)
    assert management_lock_matches(4,root,owner_uid=owner)
    with (root/"management.lock").open("r+") as stream:
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: pass
        else: raise AssertionError("parent lock lost")
    (root/"action").write_text(__file__)
    if CASE=="changed": (root/"management.lock").rename(root/"retained.lock")
    return subprocess.CompletedProcess(argv,1 if CASE=="nonzero" else 0,b"")
start.run_kickstart=action
""".replace("TEST_MANAGEMENT_PATH", repr(str(management))).replace("CASE", repr(case))
    )
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("real_start_inventory", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), tmp_path, build)
    if case == "tampered":
        with (package / "client_macos_start_entry.py").open("a") as stream:
            stream.write("\n# changed after inventory\n")
    harness = tmp_path / "parent.c"
    harness.write_text(
        "#include <string.h>\n"
        f'#include "{native / "client-macos-private-lock.h"}"\n'
        f'#include "{native / "client-macos-bound-resources.h"}"\n'
        "#define RESOURCE_CHECK_LIBRARY\n"
        f'#include "{native / "client-macos-resource-check.c"}"\n'
        f'#include "{native / "client-macos-tree-owner.h"}"\n'
        f'#define TEST_TREE "{source}"\n'
        f'#include "{native / "client-macos-start-child.h"}"\n'
        'int main(int argc,char **argv) {(void)argc;int fd=acquire_private_lock(argv[1],"management.lock",getuid());'
        "if(fd<0)return 1;int result=request_bound_registered_start(argv[1],getuid(),fd,TEST_TREE,5);"
        "int status;if(waitpid(-1,&status,WNOHANG)!=-1 || errno!=ECHILD)return 2;"
        'char retained[PATH_MAX];snprintf(retained,sizeof(retained),"%s/%s",argv[1],'
        + ('"retained.lock"' if case == "changed" else '"management.lock"')
        + ");"
        "int probe=open(retained,O_RDWR);if(probe<0 || flock(probe,LOCK_EX|LOCK_NB)==0)return 3;"
        'close(probe);close(fd);printf("%d\\n",result);return 0;}\n'
    )
    binary = tmp_path / "parent"
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
        [str(binary), str(management)], check=True, capture_output=True, text=True, timeout=8
    )
    assert result.stdout == ("1\n" if case == "clear" else "0\n")
    if case == "tampered":
        assert not (management / "action").exists()
    else:
        assert (management / "action").read_text() == str(package / "__init__.py")
