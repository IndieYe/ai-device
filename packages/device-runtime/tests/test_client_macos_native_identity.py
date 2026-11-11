"""Only linked account doubles and temporary identity files; no real registration."""

import json
import os
import platform
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    (platform.system(), platform.machine()) != ("Darwin", "arm64"),
    reason="native Foundation identity",
)


@pytest.fixture
def reader(tmp_path):
    source = Path(__file__).parents[1] / "native/client-macos-identity.m"
    shim = tmp_path / "reader.m"
    shim.write_text(
        "#define getpwnam test_getpwnam\n#define IDENTITY_LIBRARY\n"
        f'#include "{source}"\n'
        "struct passwd *test_getpwnam(const char *name) {\n"
        ' static struct passwd p; p.pw_name="test";p.pw_uid=501;p.pw_gid=20;\n'
        ' p.pw_dir="/Users/test";p.pw_shell="/bin/zsh";\n'
        ' return strcmp(name,"test") ? NULL : &p;\n}\n'
        "int main(int argc,char **argv) { @autoreleasepool {\n"
        " if(argc!=2)return 64;int lock=acquireManagement(argv[1],getuid());\n"
        " if(lock<0)return 70;NSDictionary *i=readIdentity(argv[1],getuid());\n"
        " if(!i)return 70;NSDictionary *paths=managedPaths(i);\n"
        ' if(![paths[@"program"] isEqual:@"/opt/ai-device-client"] ||\n'
        ' ![paths[@"runtime_lock"] isEqual:@"/Users/test/Library/Application Support/ai-device-client/runtime.lock"] ||\n'
        ' ![paths[@"service"] isEqual:@"gui/501/local.ai-device.client"] ||\n'
        ' ![paths[@"management_owner"] isEqual:@0] || ![paths[@"runtime_owner"] isEqual:@501])return 81;\n'
        " return 0;\n}}\n"
    )
    binary = tmp_path / "reader"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fobjc-arc",
            "-O2",
            "-arch",
            "arm64",
            "-framework",
            "Foundation",
            str(shim),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    return binary


@pytest.fixture
def identity(tmp_path):
    directory = tmp_path / "management"
    directory.mkdir(mode=0o700)
    (directory / "management.lock").touch(mode=0o600)
    return directory, {
        "schema_version": 1,
        "user": "test",
        "uid": 501,
        "gid": 20,
        "home": "/Users/test",
    }


def invoke(reader, directory):
    return subprocess.run(
        [str(reader), str(directory)], check=False, capture_output=True, timeout=5
    )


@pytest.mark.parametrize(
    "field,value",
    [
        (None, None),
        ("uid", True),
        ("uid", 501.0),
        ("uid", 502),
        ("gid", 21),
        ("schema_version", True),
        ("schema_version", 2),
        ("user", "other"),
        ("user", "test\n"),
        ("home", "/Users/other"),
        ("home", "/Users/../test"),
        ("home", "/var/root"),
        ("home", "/"),
        ("extra", 1),
    ],
)
def test_native_exact_identity_types_and_account_match(reader, identity, field, value):
    directory, payload = identity
    if field is not None:
        payload[field] = value
    record = directory / "registration.json"
    record.write_text(json.dumps(payload))
    record.chmod(0o600)
    before = record.read_bytes(), record.stat().st_ino
    assert invoke(reader, directory).returncode == (0 if field is None else 70)
    assert before == (record.read_bytes(), record.stat().st_ino)


@pytest.mark.parametrize(
    "kind", ["public", "fifo", "symlink", "hardlink", "large", "malformed", "parent"]
)
def test_native_invalid_identity_files_refused_without_repair(reader, identity, kind):
    directory, payload = identity
    record = directory / "registration.json"
    if kind == "fifo":
        os.mkfifo(record, 0o600)
    elif kind == "symlink":
        record.symlink_to(directory / "missing")
    else:
        record.write_text(json.dumps(payload))
        record.chmod(0o600)
        if kind == "public":
            record.chmod(0o644)
        elif kind == "hardlink":
            os.link(record, directory / "alias")
        elif kind == "large":
            record.write_text("x" * 8193)
        elif kind == "malformed":
            record.write_text("{not json")
        elif kind == "parent":
            directory.chmod(0o755)
    before = record.lstat()
    assert invoke(reader, directory).returncode == 70
    assert record.lstat().st_ino == before.st_ino


def test_native_reader_respects_python_management_lock(reader, identity):
    from device_runtime.client_macos_management import management_lock

    directory, payload = identity
    record = directory / "registration.json"
    record.write_text(json.dumps(payload))
    record.chmod(0o600)
    before = (directory / "management.lock").stat().st_ino
    with management_lock(directory, owner_uid=os.getuid(), owner_gid=os.getgid()):
        assert invoke(reader, directory).returncode == 70
    assert invoke(reader, directory).returncode == 0
    assert (directory / "management.lock").stat().st_ino == before


@pytest.mark.parametrize("kind", ["missing", "fifo", "symlink", "public", "hardlink"])
def test_native_management_lock_never_created_or_repaired(reader, identity, kind):
    directory, payload = identity
    record = directory / "registration.json"
    record.write_text(json.dumps(payload))
    record.chmod(0o600)
    lock = directory / "management.lock"
    lock.unlink()
    if kind == "fifo":
        os.mkfifo(lock, 0o600)
    elif kind == "symlink":
        lock.symlink_to(record)
    elif kind == "public":
        lock.touch(mode=0o644)
    elif kind == "hardlink":
        os.link(record, lock)
    before = lock.lstat() if kind != "missing" else None
    assert invoke(reader, directory).returncode == 70
    if before is None:
        assert not lock.exists()
    else:
        assert lock.lstat().st_ino == before.st_ino


@pytest.mark.parametrize(
    "case",
    [
        "absent",
        "valid",
        "corrupt",
        "directory",
        "symlink",
        "permissions",
        "released",
        "replaced-lock",
    ],
)
def test_registration_state_never_treats_invalid_as_first_install(identity, tmp_path, case):
    directory, value = identity
    record = directory / "registration.json"
    if case not in ("absent", "directory", "symlink"):
        record.write_text("broken" if case == "corrupt" else json.dumps(value))
        record.chmod(0o644 if case == "permissions" else 0o600)
    elif case == "directory":
        record.mkdir(mode=0o700)
    elif case == "symlink":
        alternate = directory / "retained-registration"
        alternate.write_text(json.dumps(value))
        alternate.chmod(0o600)
        record.symlink_to(alternate)
    source = Path(__file__).parents[1] / "native/client-macos-identity.m"
    harness = tmp_path / "state.m"
    harness.write_text(
        "#define getpwnam test_getpwnam\n#define IDENTITY_LIBRARY\n"
        f'#include "{source}"\n'
        "struct passwd *test_getpwnam(const char *name) {\n"
        ' static struct passwd p;p.pw_name="test";p.pw_uid=501;p.pw_gid=20;\n'
        ' p.pw_dir="/Users/test";p.pw_shell="/bin/zsh";return strcmp(name,"test")?NULL:&p;}\n'
        "int main(int argc,char **argv) {@autoreleasepool {\n"
        " (void)managedPaths;if(argc!=3)return 64;int fd=acquireManagement(argv[1],getuid());if(fd<0)return 70;\n"
        ' if(!strcmp(argv[2],"released")){close(fd);fd=-1;}\n'
        ' if(!strcmp(argv[2],"replaced-lock")) {\n'
        ' NSString *p=[[NSString stringWithUTF8String:argv[1]] stringByAppendingPathComponent:@"management.lock"];\n'
        ' if(rename([p fileSystemRepresentation],[[p stringByAppendingString:@".retained"] fileSystemRepresentation]))return 71;\n'
        " int fresh=open([p fileSystemRepresentation],O_CREAT|O_EXCL|O_RDWR,0600);if(fresh<0)return 72;close(fresh);}\n"
        ' printf("%d\\n",registration_state(argv[1],getuid(),fd));if(fd>=0)close(fd);return 0;}}\n'
    )
    binary = tmp_path / "state"
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
            str(harness),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    before = record.read_bytes() if record.is_file() else None
    result = subprocess.run(
        [str(binary), str(directory), case], check=True, capture_output=True, text=True, timeout=5
    )
    assert int(result.stdout) == (0 if case == "absent" else (1 if case == "valid" else -1))
    assert (record.read_bytes() if record.is_file() else None) == before
