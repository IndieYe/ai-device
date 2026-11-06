"""Real finite diagnostic children in temporary trees; never host discovery."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin native APIs")


@pytest.mark.parametrize(
    "case",
    [
        "clear",
        "final-clock-failed",
        "publish-clear",
        "observe-clear",
        "observe-second-fail",
        "observe-different",
        "prepare-clear",
        "prepare-second-fail",
        "publish-second-fail",
        "nonzero",
        "extra",
        "flood",
        "timeout",
        "unconfirmed",
        "changed-lock",
        "late-lock",
        "bad-resource",
    ],
)
def test_bound_conflict_child(tmp_path, case):
    native = Path(__file__).parents[1] / "native"
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("host_conflict_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    management = tmp_path / "management"
    preparing = case.startswith("prepare-")
    if not preparing:
        management.mkdir(mode=0o700)
        (management / "management.lock").touch(mode=0o600)
    source = tmp_path / "resources/client-tree"
    (source / "runtime/bin").mkdir(parents=True, mode=0o700)
    observing = case.startswith("observe-")
    combined = case.startswith(("publish-", "prepare-", "observe-"))
    diagnostic = tmp_path / "diagnostic.c"
    diagnostic.write_text(
        "#include <stdio.h>\n#include <string.h>\n#include <unistd.h>\n#include <fcntl.h>\n#include <sys/file.h>\n#include <errno.h>\n"
        "int main(int argc,char **argv) {\n"
        ' if(argc!=6 || strcmp(argv[1],"-I") || strcmp(argv[2],"-B") || strcmp(argv[3],"-m") ||\n'
        ' strcmp(argv[4],"device_runtime.client_macos_conflicts") ||\n'
        ' strcmp(argv[5],"--confirm-legacy-clients-disabled"))return 3;\n'
        + (
            f'int lock=open("{management / "management.lock"}",O_RDWR);if(lock<0)return 11;int held=flock(lock,LOCK_EX|LOCK_NB);close(lock);if(held==0 || (errno!=EWOULDBLOCK && errno!=EAGAIN))return 12;\n'
            if combined
            else ""
        )
        + (
            f'rename("{management / "management.lock"}","{management / "old.lock"}");int n=open("{management / "management.lock"}",O_CREAT|O_EXCL|O_WRONLY,0600);if(n<0)return 9;close(n);\n'
            if case == "late-lock"
            else ""
        )
        + (
            f'int marker=open("{tmp_path / "called"}",O_CREAT|O_EXCL|O_WRONLY,0600);if(marker<0)return 1;close(marker);\n'
            if case in ("publish-second-fail", "prepare-second-fail", "observe-second-fail")
            else ""
        )
        + ("usleep(500000);return 0;\n" if case == "timeout" else "")
        + ("for(int i=0;i<1024;i++)putchar(65);\n" if case == "flood" else "")
        + ('puts("extra");\n' if case == "extra" else "")
        + 'puts("AI_DEVICE_HOST_CONFLICTS_CLEAR_V1");\n'
        + ("return 1;}" if case == "nonzero" else "return 0;}")
    )
    subprocess.run(
        ["/usr/bin/xcrun", "clang", str(diagnostic), "-o", str(source / "runtime/bin/python3")],
        check=True,
        capture_output=True,
    )
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), tmp_path, build)
    if case == "bad-resource":
        (source / "runtime/bin/python3").write_bytes(b"bad")
    if observing:
        import shutil

        shutil.copytree(source, tmp_path / "program", symlinks=True)
        if case == "observe-different":
            (tmp_path / "program/runtime/bin/python3").write_bytes(b"different")
        inode_before = (tmp_path / "program").stat().st_ino
        content_before = (tmp_path / "program/runtime/bin/python3").read_bytes()
    harness = tmp_path / "host.m"
    harness.write_text(
        "#define main offline_writer_main\n#define EMBEDDED_RESOURCE_CHECK\n"
        f'#include "{native / "client-macos-writer-probe.c"}"\n#undef main\n'
        f'#include "{native / "client-macos-bound-resources.h"}"\n'
        + (
            "#include <sys/wait.h>\n#include <time.h>\n"
            "static int saw_eof=0,saw_reap=0;\n"
            "static ssize_t injected_read(int f,void *p,size_t n){ssize_t r=read(f,p,n);if(!r)saw_eof=1;return r;}\n"
            "static pid_t injected_wait(pid_t p,int *s,int o){pid_t r=waitpid(p,s,o);if(r>0)saw_reap=1;return r;}\n"
            "static int injected_clock(clockid_t c,struct timespec *t){if(saw_eof && saw_reap){errno=EIO;return -1;}return clock_gettime(c,t);}\n"
            "#define read injected_read\n#define waitpid injected_wait\n#define clock_gettime injected_clock\n"
            if case == "final-clock-failed"
            else ""
        )
        + f'#include "{native / "client-macos-host-conflicts.h"}"\n'
        "#undef read\n#undef waitpid\n#undef clock_gettime\n"
        + (
            "#define IDENTITY_LIBRARY\n"
            f'#include "{native / "client-macos-identity.m"}"\n'
            f'#include "{native / "client-macos-first-publish.h"}"\n'
            f'#include "{native / "client-macos-first-host-publish.h"}"\n'
            f'#include "{native / "client-macos-management-provision.h"}"\n'
            f'#include "{native / "client-macos-first-install.h"}"\n'
            if combined
            else ""
        )
        + (
            "int main(int argc,char **argv) {(void)argc;int fd=-1;\n"
            if preparing
            else 'int main(int argc,char **argv) {(void)argc;int fd=acquire_private_lock(argv[1],"management.lock",getuid());\n'
        )
        + ("" if preparing else "if(fd<0)return 2;\n")
        + (
            'char old[PATH_MAX],path[PATH_MAX];snprintf(path,sizeof(path),"%s/management.lock",argv[1]);\n'
            'snprintf(old,sizeof(old),"%s/old.lock",argv[1]);rename(path,old);int n=open(path,O_CREAT|O_EXCL|O_WRONLY,0600);close(n);\n'
            if case == "changed-lock"
            else ""
        )
        + (
            f'int result=observe_unregistered_program_with_host_check(argv[1],getuid(),fd,"{tmp_path / "program"}",1,2);(void)managedPaths;(void)acquireManagement;(void)publish_first_program_with_host_check;(void)prepare_and_publish_first_program;\n'
            if observing
            else f'int result=publish_first_program_with_host_check(argv[1],getuid(),fd,"{tmp_path / "program"}",1,2);(void)managedPaths;(void)acquireManagement;(void)prepare_and_publish_first_program;\n'
            if combined and not preparing
            else f'int result=prepare_and_publish_first_program(argv[1],getuid(),"{tmp_path / "program"}",1,2,&fd);(void)managedPaths;(void)acquireManagement;\n'
            if preparing
            else f"int result=check_bound_host_conflicts(argv[1],getuid(),fd,{0 if case == 'unconfirmed' else 1},{0.2 if case == 'timeout' else 2.0});\n"
        )
        + "int status; if(waitpid(-1,&status,WNOHANG)!=-1 || errno!=ECHILD)return 4;\n"
        'close(fd);printf("%d\\n",result);return 0;}\n'
    )
    binary = tmp_path / "host"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fobjc-arc",
            "-framework",
            "Foundation",
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
        [str(binary), str(management)], capture_output=True, text=True, check=True, timeout=3
    )
    assert result.stdout == (
        "1\n"
        if case == "clear"
        else "70\n"
        if case
        in (
            "publish-second-fail",
            "prepare-second-fail",
            "observe-second-fail",
            "observe-different",
        )
        else "0\n"
    )
    if observing:
        assert (tmp_path / "program").stat().st_ino == inode_before
        assert (tmp_path / "program/runtime/bin/python3").read_bytes() == content_before
        assert not list(tmp_path.glob(".ai-device-client-stage-*"))
    elif combined:
        assert (tmp_path / "program").exists() == (case in ("publish-clear", "prepare-clear"))
        assert len(list(tmp_path.glob(".ai-device-client-stage-*"))) == (
            case in ("publish-second-fail", "prepare-second-fail")
        )
