"""Create-only management preparation in private temporary directories."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin lock API")


@pytest.mark.parametrize(
    "case",
    [
        "first",
        "existing",
        "busy",
        "directory-link",
        "file",
        "lock-link",
        "fifo",
        "wide-directory",
        "wide-lock",
        "hardlink",
        "wide-parent",
    ],
)
def test_management_provision(tmp_path, case):
    directory = tmp_path / "management"
    lock = directory / "management.lock"
    if case != "first":
        directory.mkdir(mode=0o700)
        lock.touch(mode=0o600)
    if case == "directory-link":
        directory.rename(tmp_path / "real")
        directory.symlink_to(tmp_path / "real", target_is_directory=True)
    elif case == "file":
        lock.unlink()
        directory.rmdir()
        directory.write_text("preserved")
    elif case == "lock-link":
        lock.rename(directory / "real")
        lock.symlink_to(directory / "real")
    elif case == "fifo":
        lock.unlink()
        os.mkfifo(lock, 0o600)
    elif case == "wide-directory":
        directory.chmod(0o755)
    elif case == "wide-lock":
        lock.chmod(0o644)
    elif case == "hardlink":
        os.link(lock, directory / "other")
    elif case == "wide-parent":
        tmp_path.chmod(0o777)
    before = lock.lstat() if lock.exists() else None
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "management.c"
    harness.write_text(
        "#include <sys/stat.h>\n#include <sys/file.h>\n#include <fcntl.h>\n"
        "#include <unistd.h>\n#include <stdio.h>\n#include <string.h>\n#include <errno.h>\n#include <limits.h>\n"
        f'#include "{native / "client-macos-private-lock.h"}"\n'
        f'#include "{native / "client-macos-management-provision.h"}"\n'
        "int main(int argc,char **argv) {(void)argc;int old=-1;\n"
        'if(!strcmp(argv[2],"busy")){old=acquire_private_lock(argv[1],"management.lock",getuid());if(old<0)return 4;}\n'
        "int fd=provision_management_lock(argv[1],getuid());\n"
        "if(fd>=0) {\n"
        'if(!private_lock_matches(fd,argv[1],"management.lock",getuid()) || !(fcntl(fd,F_GETFD)&FD_CLOEXEC))return 5;\n'
        "if(provision_management_lock(argv[1],getuid())!=-2)return 6;\n"
        "struct stat a,b;fstat(fd,&a);close(fd);fd=provision_management_lock(argv[1],getuid());\n"
        "if(fd<0 || fstat(fd,&b) || a.st_ino!=b.st_ino || a.st_dev!=b.st_dev)return 7;close(fd);}\n"
        'if(old>=0)close(old);printf("%d\\n",fd>=0?1:fd);return 0;}\n'
    )
    binary = tmp_path / "provision"
    subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", str(harness), "-o", str(binary)],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [str(binary), str(directory), case], check=True, capture_output=True, text=True, timeout=3
    )
    assert int(result.stdout) == (
        1 if case in ("first", "existing") else -2 if case == "busy" else -1
    )
    if before:
        after = lock.lstat()
        assert (after.st_ino, after.st_dev, after.st_mode) == (
            before.st_ino,
            before.st_dev,
            before.st_mode,
        )
    if case == "file":
        assert directory.read_text() == "preserved"
