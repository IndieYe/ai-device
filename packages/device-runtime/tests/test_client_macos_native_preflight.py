"""Linked test-only passwd identity; real temporary files/locks, no service."""

import fcntl
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest
from test_client_macos_launcher import launcher as bound_launcher  # noqa: F401

pytestmark = pytest.mark.skipif(
    (platform.system(), platform.machine()) != ("Darwin", "arm64"), reason="Darwin native preflight"
)


@pytest.fixture
def environment(tmp_path):
    import tempfile

    temporary = tempfile.TemporaryDirectory(prefix="d-guards-")
    tmp_path = Path(temporary.name)
    management = tmp_path / "management"
    management.mkdir(mode=0o700)
    # Foundation standardizes the macOS /private/tmp alias to /tmp.
    # Match the account database's canonical spelling, not pytest's realpath.
    home = Path(str(tmp_path).replace("/private/tmp/", "/tmp/", 1)) / "home"
    directory = home / "Library/Application Support/ai-device-client"
    directory.mkdir(parents=True, mode=0o700)
    (management / "management.lock").touch(mode=0o600)
    for name in ("start.lock", "runtime.lock"):
        (directory / name).touch(mode=0o600)
    record = management / "registration.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "user": "test",
                "uid": os.getuid(),
                "gid": os.getgid(),
                "home": str(home),
            }
        )
    )
    record.chmod(0o600)
    from device_runtime.client_macos_layout import MacClientLayout

    layout = MacClientLayout(os.getuid(), home)
    layout.plist.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    layout.plist.write_bytes(layout.render())
    layout.plist.chmod(0o600)
    yield management, home, directory
    temporary.cleanup()


@pytest.fixture
def executable(tmp_path):
    source = Path(__file__).parents[1] / "native/client-macos-install-preflight.m"
    harness = tmp_path / "harness.m"
    harness.write_text(
        "#define getpwnam test_getpwnam\n#define INSTALL_PREFLIGHT_LIBRARY\n"
        f'#include "{source}"\n'
        "struct passwd *test_getpwnam(const char *name) {\n"
        ' static struct passwd p; p.pw_name="test";p.pw_uid=getuid();p.pw_gid=getgid();\n'
        ' p.pw_dir=getenv("TEST_HOME");p.pw_shell="/bin/zsh";\n'
        ' return strcmp(name,"test") ? NULL : &p;\n}\n'
        "int main(int argc,char **argv) { @autoreleasepool {\n"
        " if(argc!=2)return 64;struct managed_guards g;\n"
        " NSDictionary *paths=prepare_registered(argv[1],getuid(),&g);\n"
        " if(!paths)return 70;\n"
        ' const char *dir=[paths[@"directory"] fileSystemRepresentation];\n'
        ' if(acquire_private_lock(dir,"start.lock",getuid())!=-2)return 81;\n'
        ' if(acquire_private_lock(dir,"runtime.lock",getuid())!=-2)return 82;\n'
        " if(acquireManagement(argv[1],getuid())!=-2)return 83;\n"
        " release_guards(&g);return 0;\n}}\n"
    )
    binary = tmp_path / "preflight"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-fobjc-arc",
            "-arch",
            "arm64",
            "-framework",
            "Foundation",
            str(harness),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    return binary


def invoke(executable, environment):
    management, home, _ = environment
    return subprocess.run(
        [str(executable), str(management)],
        env={**os.environ, "TEST_HOME": str(home)},
        check=False,
        capture_output=True,
        timeout=5,
    )


def test_identity_paths_and_three_locks_remain_held_until_release(executable, environment):
    management, _, directory = environment
    paths = [management / "management.lock", directory / "start.lock", directory / "runtime.lock"]
    before = [p.stat().st_ino for p in paths]
    result = invoke(executable, environment)
    assert result.returncode == 0, result.stderr
    assert before == [p.stat().st_ino for p in paths]
    for p in paths:
        with p.open("rb+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("name", ["management.lock", "start.lock", "runtime.lock"])
def test_busy_refuses_and_releases_preceding_guards(executable, environment, name):
    management, _, directory = environment
    path = (management if name == "management.lock" else directory) / name
    with path.open("rb+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert invoke(executable, environment).returncode == 70
    assert invoke(executable, environment).returncode == 0


@pytest.mark.parametrize("kind", ["missing", "public", "symlink", "fifo", "identity"])
def test_invalid_user_lock_or_identity_never_repaired(executable, environment, kind):
    management, _, directory = environment
    path = directory / "start.lock"
    if kind == "identity":
        (management / "registration.json").write_text("{}")
    else:
        path.unlink()
        if kind == "public":
            path.touch(mode=0o644)
        elif kind == "symlink":
            path.symlink_to(directory / "runtime.lock")
        elif kind == "fifo":
            os.mkfifo(path, 0o600)
    before = path.lstat().st_ino if kind != "missing" else None
    assert invoke(executable, environment).returncode == 70
    assert (path.lstat().st_ino if kind != "missing" else None) == before
    if kind == "missing":
        assert not path.exists()


@pytest.mark.parametrize("modified", [False, True])
@pytest.mark.parametrize("with_running", [False, True])
def test_registered_guards_continuous_through_embedded_checked_publication(
    environment, tmp_path, modified, with_running
):
    import importlib.util

    management, home, directory = environment
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    (source / "version").write_text("B")
    dest = tmp_path / "destination"
    dest.mkdir(mode=0o700)
    (dest / "current").mkdir(mode=0o700)
    (dest / "current/version").write_text("A")
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("guarded_publish_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), tmp_path, build)
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "guarded-writer.m"
    harness.write_text(
        "#define main offline_writer_main\n#define EMBEDDED_RESOURCE_CHECK\n"
        f'#include "{native / "client-macos-writer-probe.c"}"\n'
        "#undef main\n#include <poll.h>\n#define getpwnam test_getpwnam\n#define INSTALL_PREFLIGHT_LIBRARY\n"
        f'#include "{native / "client-macos-install-preflight.m"}"\n'
        "struct passwd *test_getpwnam(const char *name) {\n"
        ' static struct passwd p;p.pw_name="test";p.pw_uid=getuid();p.pw_gid=getgid();\n'
        ' p.pw_dir=getenv("TEST_HOME");p.pw_shell="/bin/zsh";\n'
        ' return strcmp(name,"test") ? NULL : &p;\n}\n'
        "int main(int argc,char **argv) { @autoreleasepool {\n"
        " if(argc!=4)return 64;(void)prepare_registered;struct managed_guards g;\n"
        " NSDictionary *paths=prepare_start_barrier(argv[1],getuid(),&g);\n"
        " if(!paths)return 70;\n"
        ' const char *dir=[paths[@"directory"] fileSystemRepresentation];\n'
        ' puts("GATED");fflush(stdout);struct pollfd input={0,POLLIN,0};char c;\n'
        " if(poll(&input,1,5000)<=0 || read(0,&c,1)!=1 || c!=120) {release_guards(&g);return 87;}\n"
        " if(!acquire_registered_runtime(argv[1],getuid(),paths,&g)) {release_guards(&g);return 88;}\n"
        " int result=publish_guarded_tree(argv[2],argv[3]);\n"
        ' if(acquire_private_lock(dir,"start.lock",getuid())!=-2)return 81;\n'
        ' if(acquire_private_lock(dir,"runtime.lock",getuid())!=-2)return 82;\n'
        " if(acquireManagement(argv[1],getuid())!=-2)return 83;\n"
        " release_guards(&g);return result;\n}}\n"
    )
    binary = tmp_path / "guarded-publish"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-fobjc-arc",
            "-arch",
            "arm64",
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
    if modified:
        (source / "version").write_text("changed after compile")
    import select
    import sys
    import threading

    from test_client_macos_lease_process import RUNTIME_CHILD

    from device_runtime.client_macos_lease import request_original_exit
    from device_runtime.client_macos_lock import MacLockBusy
    from device_runtime.client_macos_start_gate import startup_handoff
    from device_runtime.maintenance import idle_lease

    runtime = None
    reaper = None
    writer = None
    try:
        if with_running:
            runtime = subprocess.Popen(
                [sys.executable, "-c", RUNTIME_CHILD, str(directory)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            reaper = threading.Thread(target=runtime.wait)
            reaper.start()
            assert select.select([runtime.stdout], [], [], 5)[0]
            assert json.loads(runtime.stdout.readline())["pid"] == runtime.pid
        writer = subprocess.Popen(
            [str(binary), str(management), str(source), str(dest)],
            env={**os.environ, "TEST_HOME": str(home)},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert select.select([writer.stdout], [], [], 5)[0]
        assert writer.stdout.readline() == b"GATED\n"
        with pytest.raises(MacLockBusy), startup_handoff(directory, uid=os.getuid()):
            pytest.fail("successor crossed registered native gate")
        if runtime is not None:
            with idle_lease(directory / "maintenance.sock") as lease:
                assert request_original_exit(lease, timeout=5).pid == runtime.pid
            assert json.loads((directory / "runtime-result.json").read_text()) == [
                "socket_closed",
                "executor_closed",
                "journal_closed",
            ]
        writer.stdin.write(b"x")
        writer.stdin.flush()
        stdout, stderr = writer.communicate(timeout=10)
        result = subprocess.CompletedProcess(writer.args, writer.returncode, stdout, stderr)
    finally:
        if writer is not None:
            writer.communicate(timeout=10)
        if reaper is not None:
            reaper.join(timeout=10)
            assert not reaper.is_alive()
            runtime.stdout.close()
            runtime.stderr.close()
    assert result.returncode == (70 if modified else 0), result.stderr
    assert (dest / "current/version").read_text() == ("A" if modified else "B")
    for path in [
        management / "management.lock",
        directory / "start.lock",
        directory / "runtime.lock",
    ]:
        with path.open("rb+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_native_start_barrier_survives_busy_runtime_until_explicit_release(environment, tmp_path):
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "barrier.m"
    harness.write_text(
        "#define getpwnam test_getpwnam\n#define INSTALL_PREFLIGHT_LIBRARY\n"
        f'#include "{native / "client-macos-install-preflight.m"}"\n'
        "struct passwd *test_getpwnam(const char *name) {\n"
        ' static struct passwd p;p.pw_name="test";p.pw_uid=getuid();p.pw_gid=getgid();\n'
        ' p.pw_dir=getenv("TEST_HOME");p.pw_shell="/bin/zsh";\n'
        ' return strcmp(name,"test") ? NULL : &p;\n}\n'
        "int main(int argc,char **argv) { @autoreleasepool {\n"
        " if(argc!=2)return 64;struct managed_guards g;\n"
        " NSDictionary *paths=prepare_start_barrier(argv[1],getuid(),&g);\n"
        " if(!paths)return 70;\n"
        ' const char *dir=[paths[@"directory"] fileSystemRepresentation];\n'
        " if(acquire_registered_runtime(argv[1],getuid(),paths,&g))return 81;\n"
        ' if(acquire_private_lock(dir,"start.lock",getuid())!=-2)return 82;\n'
        " if(acquireManagement(argv[1],getuid())!=-2)return 83;\n"
        " release_guards(&g);\n"
        " if(!prepare_registered(argv[1],getuid(),&g))return 0;\n"
        " release_guards(&g);return 84;\n}}\n"
    )
    binary = tmp_path / "barrier"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-Wno-unused-function",
            "-fobjc-arc",
            "-arch",
            "arm64",
            "-framework",
            "Foundation",
            str(harness),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    with (environment[2] / "runtime.lock").open("rb+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = invoke(binary, environment)
    assert result.returncode == 0, result.stderr
    for path in [environment[0] / "management.lock", environment[2] / "start.lock"]:
        with path.open("rb+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_full_identity_change_between_barrier_and_runtime_is_refused(environment, tmp_path):
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "identity-change.m"
    harness.write_text(
        "#define getpwnam test_getpwnam\n#define INSTALL_PREFLIGHT_LIBRARY\n"
        f'#include "{native / "client-macos-install-preflight.m"}"\n'
        "static gid_t selected_gid;\n"
        "struct passwd *test_getpwnam(const char *name) {\n"
        ' static struct passwd p;p.pw_name="test";p.pw_uid=getuid();p.pw_gid=selected_gid;\n'
        ' p.pw_dir=getenv("TEST_HOME");p.pw_shell="/bin/zsh";\n'
        ' return strcmp(name,"test") ? NULL : &p;\n}\n'
        "int main(int argc,char **argv) { @autoreleasepool {\n"
        " if(argc!=2)return 64;selected_gid=getgid();struct managed_guards g;\n"
        " NSDictionary *paths=prepare_start_barrier(argv[1],getuid(),&g);\n"
        " if(!paths)return 70;\n"
        " NSMutableDictionary *changed=[g.identity mutableCopy];\n"
        ' selected_gid++;changed[@"gid"]=@(selected_gid);\n'
        ' NSString *record=[[NSString stringWithUTF8String:argv[1]] stringByAppendingPathComponent:@"registration.json"];\n'
        " NSData *data=[NSJSONSerialization dataWithJSONObject:changed options:0 error:NULL];\n"
        " if(![data writeToFile:record atomically:NO])return 81;\n"
        " if(!readIdentity(argv[1],getuid()))return 82;\n"
        " if(![managedPaths(changed) isEqualToDictionary:paths])return 83;\n"
        " if(acquire_registered_runtime(argv[1],getuid(),paths,&g))return 84;\n"
        " if(g.runtime>=0)return 85;\n"
        " release_guards(&g);\n"
        " if(!prepare_registered(argv[1],getuid(),&g))return 86;\n"
        " release_guards(&g);return 0;\n}}\n"
    )
    binary = tmp_path / "identity-change"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-fobjc-arc",
            "-arch",
            "arm64",
            "-framework",
            "Foundation",
            str(harness),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    result = invoke(binary, environment)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "case",
    [
        "exact",
        "changed",
        "absent",
        "released",
        "identity",
        "publish",
        "repair-broken-python",
        "repair-missing-python",
        "repair-full-tree",
        "first",
        "bad-source",
        "source-write",
        "mid-identity",
        "replaced-management",
        "replaced-start",
        "replaced-runtime",
        "handoff",
        "handoff-invalid",
        "ready-after-handoff",
        "ready-identity-change",
        "publish-released",
        "before-copy",
        "after-swap",
    ],
)
def test_registered_recovery_observation_never_replays(environment, tmp_path, case):
    import importlib.util

    full_repair = case == "repair-full-tree"
    if full_repair and not os.environ.get("AI_DEVICE_TEST_MAC_TREE"):
        pytest.skip("explicit packaged tree required")
    management, home, directory = environment
    target = tmp_path / "public"
    target.mkdir(mode=0o700)
    if full_repair:
        shutil.copytree(os.environ["AI_DEVICE_TEST_MAC_TREE"], target, symlinks=True, dirs_exist_ok=True)
    (target / "version").write_text("B")
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("recovery_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    build = tmp_path / "compile"
    manifest = builder.INVENTORY.inventory(target)
    if case == "source-write":
        (target / "version").chmod(0o664)
        with pytest.raises(ValueError, match="metadata invalid"):
            builder.INVENTORY.inventory(target)
        # Deliberately bypass normal build validation for this native rejection test.
        next(entry for entry in manifest["entries"] if entry["path"] == "version")["mode"] = 0o664
    builder.compile_resource_check(manifest, tmp_path, build)
    source = tmp_path / "resources/client-tree"
    source.mkdir(parents=True, mode=0o700)
    if full_repair:
        shutil.copytree(target, source, symlinks=True, dirs_exist_ok=True)
    (source / "version").write_text("X" if case == "bad-source" else "B")
    (source / "version").chmod((target / "version").stat().st_mode & 0o777)
    if case in (
        "changed",
        "publish",
        "bad-source",
        "source-write",
        "mid-identity",
        "publish-released",
        "before-copy",
        "after-swap",
    ):
        (target / "version").write_text("A")
    elif case in ("absent", "first"):
        # No existing artifact is removed: select an initially absent target.
        target = tmp_path / "absent"
    repair = case.startswith("repair-")
    if repair:
        (target / "version").write_text("A")
        damaged_bin = target / "runtime/bin"
        damaged_bin.mkdir(parents=True, exist_ok=True)
        if full_repair:
            old_python = damaged_bin / "python3"
            old_python.rename(damaged_bin / "python3.retained")
            old_python.write_text(f"#!/bin/sh\nprintf invoked > '{tmp_path / 'old-python-called'}'\nexit 91\n")
            old_python.chmod(0o700)
        if case == "repair-broken-python":
            (damaged_bin / "python3").write_bytes(b"not an executable interpreter\n")
            (damaged_bin / "python3").chmod(0o700)
    registration_before = (management / "registration.json").read_bytes()
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "recovery.m"
    harness.write_text(
        "#define main offline_writer_main\n#define EMBEDDED_RESOURCE_CHECK\n"
        f'#include "{native / "client-macos-writer-probe.c"}"\n'
        "#undef main\n"
        "#define getpwnam test_getpwnam\n#define INSTALL_PREFLIGHT_LIBRARY\n"
        "#define managedPaths productionManagedPaths\n"
        f'#include "{native / "client-macos-install-preflight.m"}"\n'
        "#undef managedPaths\n"
        "static NSDictionary *managedPaths(NSDictionary *identity) {\n"
        " NSMutableDictionary *p=[productionManagedPaths(identity) mutableCopy];\n"
        ' p[@"program"]=[NSString stringWithUTF8String:getenv("TEST_TARGET")];\n'
        ' p[@"management_owner"]=@(getuid());return p;\n}\n'
        f'#include "{native / "client-macos-recovery-check.h"}"\n'
        "static int recheck_calls;\n"
        "static int test_publication_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g) {\n"
        ' if(++recheck_calls==2 && !strcmp(getenv("TEST_CASE"),"mid-identity"))g->identity=@{};\n'
        " return registered_guards_current(m,u,p,g);}\n"
        "#define registered_guards_current test_publication_current\n"
        f'#include "{native / "client-macos-registered-publish.h"}"\n'
        "#undef registered_guards_current\n"
        f'#include "{native / "client-macos-bound-resources.h"}"\n'
        f'#include "{native / "client-macos-lease-request.m"}"\n'
        f'#include "{native / "client-macos-ready-request.h"}"\n'
        "static int test_ready_response(int fd,uid_t uid,NSString *v,NSString *b,double d) {\n"
        " (void)fd;(void)uid;(void)v;(void)b;(void)d;\n"
        ' if(!strcmp(getenv("TEST_CASE"),"ready-identity-change")) {\n'
        ' NSString *path=[[NSString stringWithUTF8String:getenv("TEST_MANAGEMENT")] stringByAppendingPathComponent:@"registration.json"];\n'
        " NSData *data=[NSData dataWithContentsOfFile:path];\n"
        " NSMutableDictionary *value=[[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] mutableCopy];\n"
        ' value[@"gid"]=@([value[@"gid"] unsignedIntValue]+1);\n'
        " [[NSJSONSerialization dataWithJSONObject:value options:0 error:NULL] writeToFile:path atomically:NO]; }\n"
        " return 1;}\n"
        "#define ready_request_connected test_ready_response\n"
        f'#include "{native / "client-macos-start-handoff.h"}"\n'
        "#undef ready_request_connected\n"
        "struct passwd *test_getpwnam(const char *name) {\n"
        ' static struct passwd p;p.pw_name="test";p.pw_uid=getuid();p.pw_gid=getgid();\n'
        ' p.pw_dir=getenv("TEST_HOME");p.pw_shell="/bin/zsh";\n'
        ' return strcmp(name,"test") ? NULL : &p;\n}\n'
        "int main(int argc,char **argv) { @autoreleasepool {\n"
        " (void)query_registered_ready;\n"
        " (void)lease_request_connected;(void)lease_wait_original_exit;(void)ready_request_connected;\n"
        " if(argc!=3)return 64;struct managed_guards g;\n"
        " if(!prepare_registered(argv[1],getuid(),&g))return 70;\n"
        " NSDictionary *paths=managedPaths(g.identity);\n"
        ' if(!strncmp(argv[2],"replaced-",9)) {\n'
        ' NSString *name=!strcmp(argv[2],"replaced-management")?@"management.lock":\n'
        ' (!strcmp(argv[2],"replaced-start")?@"start.lock":@"runtime.lock");\n'
        ' NSString *base=!strcmp(argv[2],"replaced-management")?[NSString stringWithUTF8String:argv[1]]:paths[@"directory"];\n'
        ' NSString *lock=[base stringByAppendingPathComponent:name],*old=[lock stringByAppendingString:@".retained"];\n'
        " if(rename([lock fileSystemRepresentation],[old fileSystemRepresentation]))return 98;\n"
        " int fresh=open([lock fileSystemRepresentation],O_CREAT|O_EXCL|O_RDWR,0600);if(fresh<0)return 99;close(fresh);\n"
        " if(registered_target_exact(argv[1],getuid(),paths,&g) || handoff_registered_start(argv[1],getuid(),paths,&g))return 100;\n"
        " char source_path[PATH_MAX];if(!locate_bound_resources(source_path,getuid()))return 101;\n"
        " if(!publish_registered_target(argv[1],getuid(),paths,&g,source_path))return 102;\n"
        ' release_guards(&g);puts("UNKNOWN_READY_NOT_CHECKED");return 0;}\n'
        ' if(!strcmp(argv[2],"ready-after-handoff") || !strcmp(argv[2],"ready-identity-change")) {\n'
        ' if(ready_registered_connected(-1,argv[1],getuid(),paths,&g,@"B",@"build",lease_clock()+1))return 94;\n'
        " if(!handoff_registered_start(argv[1],getuid(),paths,&g))return 95;\n"
        ' int ready=ready_registered_connected(-1,argv[1],getuid(),paths,&g,@"B",@"build",lease_clock()+1);\n'
        ' if(ready!=(!strcmp(argv[2],"ready-after-handoff")))return 96;\n'
        " if(acquireManagement(argv[1],getuid())!=-2)return 97;release_guards(&g);return 0;}\n"
        ' if(!strcmp(argv[2],"handoff") || !strcmp(argv[2],"handoff-invalid")) {\n'
        ' int invalid=!strcmp(argv[2],"handoff-invalid");if(invalid)g.identity=@{};\n'
        " int handed=handoff_registered_start(argv[1],getuid(),paths,&g);\n"
        " if(handed==invalid)return 91;\n"
        " if(acquireManagement(argv[1],getuid())!=-2)return 92;\n"
        ' const char *dir=[paths[@"directory"] fileSystemRepresentation];\n'
        ' int s=acquire_private_lock(dir,"start.lock",getuid());\n'
        ' int r=acquire_private_lock(dir,"runtime.lock",getuid());\n'
        " if(invalid ? (s!=-2 || r!=-2) : (s<0 || r<0))return 93;\n"
        " if(s>=0)close(s);if(r>=0)close(r);release_guards(&g);return 0;}\n"
        ' if(!strcmp(argv[2],"released") || !strcmp(argv[2],"publish-released"))release_guards(&g);\n'
        ' if(!strcmp(argv[2],"identity"))g.identity=@{};\n'
        ' if(!strcmp(argv[2],"publish") || !strncmp(argv[2],"repair-",7) || !strcmp(argv[2],"first") ||\n'
        '    !strcmp(argv[2],"mid-identity") || !strcmp(argv[2],"source-write") || !strcmp(argv[2],"bad-source") || !strcmp(argv[2],"publish-released") ||\n'
        '    !strcmp(argv[2],"before-copy") || !strcmp(argv[2],"after-swap")) {\n'
        ' if(!strcmp(argv[2],"before-copy") || !strcmp(argv[2],"after-swap"))fault=argv[2];\n'
        " char source_path[PATH_MAX];if(!locate_bound_resources(source_path,getuid()))return 89;\n"
        " int result=publish_registered_target(argv[1],getuid(),paths,&g,source_path);\n"
        ' int rejected=!strcmp(argv[2],"mid-identity") || !strcmp(argv[2],"source-write") || !strcmp(argv[2],"bad-source") || !strcmp(argv[2],"publish-released");\n'
        " if((result!=0)!=rejected)return 85; }\n"
        " int exact=registered_target_exact(argv[1],getuid(),paths,&g);\n"
        " if(exact!=registered_target_exact(argv[1],getuid(),paths,&g))return 81;\n"
        ' if(strcmp(argv[2],"released") && strcmp(argv[2],"publish-released")) {\n'
        ' const char *dir=[paths[@"directory"] fileSystemRepresentation];\n'
        ' if(acquire_private_lock(dir,"start.lock",getuid())!=-2)return 82;\n'
        ' if(acquire_private_lock(dir,"runtime.lock",getuid())!=-2)return 83;\n'
        " if(acquireManagement(argv[1],getuid())!=-2)return 84; }\n"
        " release_guards(&g);\n"
        ' puts(exact ? "TARGET_EXACT_READY_NOT_CHECKED" : "UNKNOWN_READY_NOT_CHECKED");\n'
        " return 0;\n}}\n"
    )
    binary = tmp_path / "recovery"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-fobjc-arc",
            "-arch",
            "arm64",
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
    before = (target / "version").read_bytes() if target.exists() else None
    if case in ("before-copy", "after-swap"):
        crashed = subprocess.run(
            [str(binary), str(management), case],
            check=False,
            env={
                **os.environ,
                "TEST_HOME": str(home),
                "TEST_TARGET": str(target),
                "TEST_SOURCE": str(source),
                "TEST_CASE": case,
            },
            capture_output=True,
            timeout=5,
        )
        assert crashed.returncode == 86, crashed.stderr
    result = subprocess.run(
        [
            str(binary),
            str(management),
            "observe" if case in ("before-copy", "after-swap") else case,
        ],
        env={
            **os.environ,
            "TEST_HOME": str(home),
            "TEST_TARGET": str(target),
            "TEST_SOURCE": str(source),
            "TEST_CASE": case,
            "TEST_MANAGEMENT": str(management),
        },
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    if case in ("handoff", "handoff-invalid", "ready-after-handoff", "ready-identity-change"):
        assert result.stdout == ""
        assert (target / "version").read_bytes() == before
        for path in [
            management / "management.lock",
            directory / "start.lock",
            directory / "runtime.lock",
        ]:
            with path.open("rb+") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    expected = (
        "TARGET_EXACT_READY_NOT_CHECKED"
        if repair or case in ("exact", "publish", "first", "after-swap")
        else "UNKNOWN_READY_NOT_CHECKED"
    )
    assert result.stdout.strip() == expected
    expected_bytes = b"B" if repair or case in ("publish", "first", "after-swap") else before
    assert ((target / "version").read_bytes() if target.exists() else None) == expected_bytes
    stages = list(tmp_path.glob(".ai-device-client-stage-*"))
    if repair or case in ("publish", "after-swap"):
        assert len(stages) == 1
        assert (stages[0] / "version").read_bytes() == b"A"
        if repair:
            old_python = stages[0] / "runtime/bin/python3"
            assert old_python.exists() == (full_repair or case == "repair-broken-python")
            if full_repair:
                assert b"exit 91" in old_python.read_bytes()
                assert not (tmp_path / "old-python-called").exists()
                identity = json.loads((target / "client-identity.json").read_text())
                queried = subprocess.run(
                    [str(target / "runtime/bin/python3"), "-I", "-B", "-c",
                     "import json; from device_runtime.version import runtime_version; print(json.dumps(runtime_version()))"],
                    capture_output=True, text=True, timeout=10, check=True,
                )
                assert json.loads(queried.stdout) == identity
            elif old_python.exists():
                assert old_python.read_bytes() == b"not an executable interpreter\n"
            assert (management / "registration.json").read_bytes() == registration_before
            assert (target / "runtime").exists() == full_repair
    elif case == "mid-identity":
        assert len(stages) == 1
        assert (stages[0] / "version").read_bytes() == b"B"
    else:
        assert not stages
    assert not list(tmp_path.glob("stage-*"))
    for path in [
        management / "management.lock",
        directory / "start.lock",
        directory / "runtime.lock",
    ]:
        with path.open("rb+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("case", ["ok", "missing", "symlink", "writable", "owner", "binary"])
def test_bound_resource_location_does_not_consume_environment(tmp_path, case):
    native = Path(__file__).parents[1] / "native"
    resource = tmp_path / "resources"
    resource.mkdir(mode=0o700)
    tree = resource / "client-tree"
    if case == "symlink":
        alternate = tmp_path / "alternate"
        alternate.mkdir(mode=0o700)
        tree.symlink_to(alternate, target_is_directory=True)
    elif case != "missing":
        tree.mkdir(mode=0o700)
    if case == "writable":
        resource.chmod(0o777)
    harness = tmp_path / "bound.c"
    harness.write_text(
        f'#include "{native / "client-macos-bound-resources.h"}"\n'
        "int main(void) { char path[PATH_MAX];uid_t owner=getuid();\n"
        + ("owner++;\n" if case == "owner" else "")
        + "if(!locate_bound_resources(path,owner))return 70;puts(path);return 0;}\n"
    )
    binary = tmp_path / "bound"
    subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", str(harness), "-o", str(binary)],
        check=True,
        capture_output=True,
    )
    if case == "binary":
        binary.chmod(0o777)
    result = subprocess.run(
        [str(binary)],
        env={**os.environ, "HOME": "/invalid", "TEST_SOURCE": "/invalid"},
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == (0 if case == "ok" else 70)
    if case == "ok":
        assert Path(result.stdout.strip()).resolve() == tree.resolve()


@pytest.mark.parametrize("case", ["ok", "file-write", "dir-write", "wrong-owner", "hardlink"])
def test_tree_owner_gate_without_permission_repair(tmp_path, case):
    native = Path(__file__).parents[1] / "native"
    tree = tmp_path / "tree"
    tree.mkdir(mode=0o700)
    content = tree / "content"
    content.write_text("unchanged")
    content.chmod(0o644)
    if case == "file-write":
        content.chmod(0o666)
    elif case == "dir-write":
        tree.chmod(0o777)
    elif case == "hardlink":
        os.link(content, tree / "alias")
    harness = tmp_path / "owner.c"
    harness.write_text(
        "#include <unistd.h>\n"
        f'#include "{native / "client-macos-tree-owner.h"}"\n'
        "int main(int argc,char **argv) {if(argc!=2)return 64;\n"
        + ("uid_t owner=getuid()+1;\n" if case == "wrong-owner" else "uid_t owner=getuid();\n")
        + "return tree_owner_valid(argv[1],owner,0)?0:70;}\n"
    )
    binary = tmp_path / "owner"
    subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", str(harness), "-o", str(binary)],
        check=True,
        capture_output=True,
    )
    before = (content.stat().st_mode, tree.stat().st_mode, content.stat().st_uid)
    result = subprocess.run([str(binary), str(tree)], check=False, capture_output=True, timeout=5)
    assert result.returncode == (0 if case == "ok" else 70)
    assert (content.stat().st_mode, tree.stat().st_mode, content.stat().st_uid) == before
    assert content.read_text() == "unchanged"


@pytest.mark.parametrize(
    "case",
    [
        "running",
        "missing",
        "released",
        "publish",
        "bad-resource",
        "publish-ready",
        "bound-ready",
        "bound-launcher",
        "bound-helper",
    ],
)
def test_registered_native_exit_acquires_runtime_without_python_coordinator(
    environment, tmp_path, case, request
):
    import select
    import sys
    import threading

    from test_client_macos_lease_process import RUNTIME_CHILD

    management, home, directory = environment
    import importlib.util

    if case in ("bound-launcher", "bound-helper") and not os.environ.get("AI_DEVICE_TEST_MAC_TREE"):
        pytest.skip("explicit packaged tree required")
    source = tmp_path / "resources/client-tree"
    source.mkdir(parents=True, mode=0o700)
    (source / "version").write_text("B")
    if case in ("bound-ready", "bound-launcher", "bound-helper"):
        import shutil

        from device_runtime import version as runtime_identity

        package = source / "runtime/lib/python3.13/site-packages/device_runtime"
        shutil.copytree(
            Path(runtime_identity.__file__).parent,
            package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        if case in ("bound-launcher", "bound-helper"):
            prepare_bound_launcher_tree(source, package, request.getfixturevalue("bound_launcher"))
        if case == "bound-helper":
            prepare_bound_start_helper(package, management)
        identity_script = Path(__file__).parents[1] / "scripts/build-client-tree.py"
        identity_spec = importlib.util.spec_from_file_location(
            "bound_identity_builder", identity_script
        )
        identity_builder = importlib.util.module_from_spec(identity_spec)
        identity_spec.loader.exec_module(identity_builder)
        identity = identity_builder.write_client_identity(source)
        if case == "bound-ready":
            assert identity == runtime_identity.runtime_version()
    target = tmp_path / "program"
    target.mkdir(mode=0o700)
    (target / "version").write_text("A")
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("exit_publish_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), tmp_path, build)
    if case == "bad-resource":
        (source / "version").write_text("changed")
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "registered-exit.m"
    harness.write_text(
        "#define main offline_writer_main\n#define EMBEDDED_RESOURCE_CHECK\n"
        f'#define STAGE_SOURCE "{source}"\n'
        f'#include "{native / "client-macos-writer-probe.c"}"\n'
        "#undef main\n#define getpwnam test_getpwnam\n#define INSTALL_PREFLIGHT_LIBRARY\n"
        "#define acquire_registered_runtime productionAcquireRuntime\n"
        "#define managedPaths productionManagedPaths\n"
        f'#include "{native / "client-macos-install-preflight.m"}"\n'
        "#undef managedPaths\n"
        "static NSDictionary *managedPaths(NSDictionary *identity) {\n"
        " NSMutableDictionary *paths=[productionManagedPaths(identity) mutableCopy];\n"
        ' paths[@"program"]=[NSString stringWithUTF8String:getenv("TEST_TARGET")];\n'
        ' paths[@"management_owner"]=@(getuid());return paths;}\n'
        "#undef acquire_registered_runtime\n"
        "static int acquire_registered_runtime(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g) {\n"
        " if(![p isEqualToDictionary:managedPaths(g->identity)])return 0;\n"
        " return productionAcquireRuntime(m,u,productionManagedPaths(g->identity),g);}\n"
        f'#include "{native / "client-macos-lease-request.m"}"\n'
        f'#include "{native / "client-macos-registered-exit.h"}"\n'
        f'#include "{native / "client-macos-recovery-check.h"}"\n'
        f'#include "{native / "client-macos-registered-publish.h"}"\n'
        f'#include "{native / "client-macos-bound-resources.h"}"\n'
        f'#include "{native / "client-macos-ready-request.h"}"\n'
        f'#include "{native / "client-macos-start-handoff.h"}"\n'
        f'#include "{native / "client-macos-target-identity.h"}"\n'
        f'#include "{native / "client-macos-recovery-observe.h"}"\n'
        # No launchctl here: the relay header is skipped and the two fixed
        # functions it declares are supplied locally with test behaviour.
        "static int unload_registered_service_relay(const char *m,uid_t u,int fd,const char *t,double d) {\n"
        ' (void)m;(void)u;(void)fd;(void)d;if(!t||!*t)return 0;puts("SERVICE_UNLOADED");fflush(stdout);return 1;}\n'
        "static int load_registered_service_relay(const char *m,uid_t u,int fd,const char *t,double d) {\n"
        ' (void)m;(void)u;(void)fd;(void)d;if(!t||!*t)return 0;puts("SERVICE_LOADED");fflush(stdout);return 1;}\n'
        f'#include "{native / "client-macos-program-commit.h"}"\n'
        f'#include "{native / "client-macos-registered-upgrade.h"}"\n'
        f'#include "{native / "client-macos-start-child.h"}"\n'
        "static int test_start_child(const char *m,uid_t u,int fd,const char *tree,double timeout) {\n"
        ' (void)request_bound_registered_start;if(!tree || timeout<=0 || timeout>10 || !private_lock_matches(fd,m,"management.lock",u))return 0;\n'
        ' if(getenv("REAL_HELPER"))return request_bound_registered_start(m,u,fd,STAGE_SOURCE,timeout);\n'
        ' puts("PUBLISHED_START");fflush(stdout);\n'
        " struct pollfd input={0,POLLIN,0};char c;return poll(&input,1,5000)>0 && read(0,&c,1)==1;}\n"
        "#define request_bound_registered_start test_start_child\n"
        f'#include "{native / "client-macos-registered-start.h"}"\n'
        "#undef request_bound_registered_start\n"
        "struct passwd *test_getpwnam(const char *name) {\n"
        ' static struct passwd p;p.pw_name="test";p.pw_uid=getuid();p.pw_gid=getgid();\n'
        ' p.pw_dir=getenv("TEST_HOME");p.pw_shell="/bin/zsh";\n'
        ' return strcmp(name,"test")?NULL:&p;}\n'
        "int main(int argc,char **argv) { @autoreleasepool {\n"
        " (void)observe_registered_recovery;(void)prepare_registered;(void)query_registered_ready;if(argc!=2)return 64;struct managed_guards g;\n"
        " NSDictionary *paths=prepare_start_barrier(argv[1],getuid(),&g);if(!paths)return 70;\n"
        " paths=managedPaths(g.identity);\n"
        ' if(getenv("BOUND_READY")) {\n'
        " double deadline=lease_clock()+30;struct registered_start_context start={argv[1],getuid(),paths,&g,STAGE_SOURCE,deadline,0};\n"
        # The coordinator no longer takes a start callback at all, so this helper
        # is referenced only to keep the translation unit free of an unused
        # function under -Werror. It must never be invoked.
        " (void)request_registered_start_once;\n"
        " if(upgrade_registered_bound_once(argv[1],getuid(),paths,&g,STAGE_SOURCE,deadline)!=UPGRADE_STAGE_OK)return 84;\n"
        # The coordinator reloads via the relay, never via this kickstart
        # callback, so the context must remain completely untouched.
        " if(start.attempted)return 87;\n"
        " for(int observed=0;observed<2;observed++)if(observe_registered_recovery(argv[1],getuid(),paths,&g,lease_clock()+3)!=RECOVERY_READY_EXACT)return 88;\n"
        " if(acquireManagement(argv[1],getuid())!=-2)return 85;\n"
        ' g.start=acquire_private_lock([paths[@"directory"] fileSystemRepresentation],"start.lock",getuid());\n'
        " if(g.start<0 || exit_registered_original(argv[1],getuid(),paths,&g,lease_clock()+3)!=LEASE_ACCEPTED)return 86;\n"
        ' release_guards(&g);puts("PUBLISHED_READY_EXITED");return 0;}\n'
        ' if(getenv("RELEASE_GUARDS"))release_guards(&g);\n'
        " int result=exit_registered_original(argv[1],getuid(),paths,&g,lease_clock()+3);\n"
        ' if(getenv("EXPECT_UNKNOWN")) {int valid=result==LEASE_UNKNOWN && g.runtime<0;release_guards(&g);return valid?0:73;}\n'
        " if(result!=LEASE_ACCEPTED || g.runtime<0){release_guards(&g);return 71;}\n"
        ' const char *dir=[paths[@"directory"] fileSystemRepresentation];\n'
        " if(acquireManagement(argv[1],getuid())!=-2 ||\n"
        ' acquire_private_lock(dir,"start.lock",getuid())!=-2 ||\n'
        ' acquire_private_lock(dir,"runtime.lock",getuid())!=-2)return 72;\n'
        ' if(getenv("PUBLISH")) {\n'
        " char source_path[PATH_MAX];if(!locate_bound_resources(source_path,getuid()))return 74;\n"
        " int published=publish_registered_target(argv[1],getuid(),paths,&g,source_path);\n"
        ' int bad=getenv("BAD_RESOURCE")!=NULL;if((published!=0)!=bad)return 75;\n'
        " if(registered_target_exact(argv[1],getuid(),paths,&g)==bad)return 76;\n"
        ' if(acquireManagement(argv[1],getuid())!=-2 || acquire_private_lock(dir,"start.lock",getuid())!=-2 ||\n'
        ' acquire_private_lock(dir,"runtime.lock",getuid())!=-2)return 77;\n'
        ' if(bad) {release_guards(&g);puts("RESOURCE_REFUSED_NOT_STARTED");return 0;}\n'
        " if(!handoff_registered_start(argv[1],getuid(),paths,&g))return 78;\n"
        ' if(getenv("FULL_READY")) {puts("PUBLISHED_START");fflush(stdout);\n'
        " struct pollfd input={0,POLLIN,0};char c;if(poll(&input,1,5000)<=0 || read(0,&c,1)!=1)return 81;\n"
        ' NSString *v=[NSString stringWithUTF8String:getenv("TARGET_VERSION")],*b=[NSString stringWithUTF8String:getenv("TARGET_BUILD")];\n'
        " if(!query_registered_ready(argv[1],getuid(),paths,&g,v,b,lease_clock()+2))return 82;\n"
        ' g.start=acquire_private_lock(dir,"start.lock",getuid());\n'
        " if(g.start<0 || exit_registered_original(argv[1],getuid(),paths,&g,lease_clock()+3)!=LEASE_ACCEPTED)return 83;\n"
        ' release_guards(&g);puts("PUBLISHED_READY_EXITED");return 0;}\n'
        ' int s=acquire_private_lock(dir,"start.lock",getuid()),r=acquire_private_lock(dir,"runtime.lock",getuid());\n'
        " if(s<0 || r<0 || acquireManagement(argv[1],getuid())!=-2)return 79;close(s);close(r);\n"
        ' release_guards(&g);puts("PUBLISHED_HANDOFF_READY_NOT_CHECKED");return 0;}\n'
        ' release_guards(&g);puts("EXITED_RUNTIME_HELD_NOT_INSTALLED");return 0;}}\n'
    )
    binary = tmp_path / "registered-exit"
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
    if case not in ("running", "publish", "bad-resource", "publish-ready", "bound-ready"):
        result = subprocess.run(
            [str(binary), str(management)],
            env={
                **os.environ,
                "TEST_HOME": str(home),
                "TEST_TARGET": str(target),
                "EXPECT_UNKNOWN": "1",
                **({"RELEASE_GUARDS": "1"} if case == "released" else {}),
            },
            capture_output=True,
            check=True,
            timeout=5,
        )
        assert result.stdout == b""
        for path in [
            management / "management.lock",
            directory / "start.lock",
            directory / "runtime.lock",
        ]:
            with path.open("rb+") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    # Preserve runtime.lock in the registered directory; control socket belongs in state.
    (directory / "state").mkdir(mode=0o700)
    child_source = RUNTIME_CHILD.replace("state_dir=root,", "state_dir=root/'state',")
    child = subprocess.Popen(
        [sys.executable, "-c", child_source, str(directory)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    reaper = threading.Thread(target=child.wait)
    reaper.start()
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        line = child.stdout.readline()
        assert line, child.stderr.read()
        assert json.loads(line)["pid"] == child.pid
        second = None
        second_reaper = None
        if case in ("publish-ready", "bound-ready", "bound-launcher", "bound-helper"):
            from device_runtime.version import runtime_version

            version = runtime_version()
            watcher = subprocess.Popen(
                [str(binary), str(management)],
                env={
                    **os.environ,
                    "TEST_HOME": str(home),
                    "TEST_TARGET": str(target),
                    "PUBLISH": "1",
                    "FULL_READY": "1",
                    **({"REAL_HELPER": "1"} if case == "bound-helper" else {}),
                    **(
                        {"BOUND_READY": "1"}
                        if case in ("bound-ready", "bound-launcher", "bound-helper")
                        else {
                            "TARGET_VERSION": version["version"],
                            "TARGET_BUILD": version["build_id"],
                        }
                    ),
                },
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                if case == "bound-helper":
                    import time

                    deadline = time.monotonic() + 20
                    while (
                        not (management / "helper-request").exists() and time.monotonic() < deadline
                    ):
                        time.sleep(0.01)
                    assert (management / "helper-request").exists()
                elif case == "bound-ready":
                    # New ordering: the coordinator prints the unload and load
                    # stages from their substitutes, in that order, and only
                    # then reports completion. No kickstart is involved.
                    lines = []
                    deadline_read = __import__("time").monotonic() + 25
                    while len(lines) < 2 and __import__("time").monotonic() < deadline_read:
                        if not select.select([watcher.stdout], [], [], 0.5)[0]:
                            continue
                        line = watcher.stdout.readline().strip()
                        if not line:
                            break
                        lines.append(line)
                    print("WATCHER_RC",watcher.poll(),"LINES",lines,"ERR",watcher.stderr.read() if watcher.poll() is not None else b"")
                    assert lines == [b"SERVICE_UNLOADED", b"SERVICE_LOADED"], (
                        lines,
                        watcher.returncode,
                        watcher.stderr.read() if watcher.poll() is not None else b"",
                    )
                else:
                    assert select.select([watcher.stdout], [], [], 25)[0]
                    assert watcher.stdout.readline().strip() == b"PUBLISHED_START"
                command = (
                    [str(target / "bin/ai-device-client-launcher")]
                    if case in ("bound-launcher", "bound-helper")
                    else [sys.executable, "-c", child_source, str(directory)]
                )
                second = subprocess.Popen(
                    command,
                    env={
                        **os.environ,
                        "TEST_HOME": str(home),
                        "TEST_MODULE_PYTHON": str(target / "runtime/bin/python3"),
                    },
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                second_reaper = threading.Thread(target=second.wait)
                second_reaper.start()
                assert select.select([second.stdout], [], [], 5)[0]
                launched = json.loads(second.stdout.readline())
                assert launched["pid"] == second.pid
                if case in ("bound-launcher", "bound-helper"):
                    assert (
                        Path(launched["package"]).resolve()
                        == (
                            target
                            / "runtime/lib/python3.13/site-packages/device_runtime/__init__.py"
                        ).resolve()
                    )
                    assert launched["identity"] == identity
                if case == "bound-helper":
                    (management / "helper-ack").touch()
                elif watcher.poll() is None:
                    watcher.stdin.write(b"x")
                    watcher.stdin.flush()
                # A coordinator that already exited (new reload ordering) needs
                # no acknowledgement; its exit status is checked below.
                out, err = watcher.communicate(timeout=7)
                assert watcher.returncode == 0, err
                if case == "bound-helper":
                    from device_runtime.client_macos_processes import inspect_identity

                    helper_pid = int((management / "helper-request").read_text())
                    assert inspect_identity(helper_pid) is None
                result = subprocess.CompletedProcess(
                    watcher.args, watcher.returncode, out.decode(), err.decode()
                )
            finally:
                if watcher.poll() is None:
                    watcher.wait(timeout=10)
                if watcher.stdin and not watcher.stdin.closed:
                    watcher.stdin.close()
                watcher.stdout.close()
                watcher.stderr.close()
                if second_reaper:
                    second_reaper.join(timeout=10)
                    assert not second_reaper.is_alive()
                    assert second.returncode == 0, second.stderr.read()
                    if case in ("bound-launcher", "bound-helper"):
                        assert json.loads(
                            (directory / "bound-launcher-result.json").read_text()
                        ) == [
                            "connection_closed",
                            "executor_closed",
                            "disconnected",
                            "journal_closed",
                            "fd-closed",
                        ]
                    second.stdout.close()
                    second.stderr.close()
        else:
            result = subprocess.run(
                [str(binary), str(management)],
                env={
                    **os.environ,
                    "TEST_HOME": str(home),
                    "TEST_TARGET": str(target),
                    **({"PUBLISH": "1"} if case in ("publish", "bad-resource") else {}),
                    **({"BAD_RESOURCE": "1"} if case == "bad-resource" else {}),
                },
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
        expected = {
            "publish": "PUBLISHED_HANDOFF_READY_NOT_CHECKED",
            "publish-ready": "PUBLISHED_READY_EXITED",
            "bound-ready": "PUBLISHED_READY_EXITED",
            "bound-launcher": "PUBLISHED_READY_EXITED",
            "bound-helper": "PUBLISHED_READY_EXITED",
            "bad-resource": "RESOURCE_REFUSED_NOT_STARTED",
        }.get(case, "EXITED_RUNTIME_HELD_NOT_INSTALLED")
        assert result.stdout.strip() == expected
        assert (target / "version").read_text() == (
            "B"
            if case in ("publish", "publish-ready", "bound-ready", "bound-launcher", "bound-helper")
            else "A"
        )
        stages = list(tmp_path.glob(".ai-device-client-stage-*"))
        if case in ("bound-ready", "bound-launcher", "bound-helper"):
            # The registered upgrade publishes with one RENAME_SWAP, not a copy
            # through a pid-named stage directory. That is why no
            # `.ai-device-client-stage-*` exists here: the previous tree ends up
            # at the source it was swapped from, which is the only place it can
            # be inspected afterwards.
            assert not stages
            assert (source / "version").read_text() == "A"
        elif case in ("publish", "publish-ready"):
            assert len(stages) == 1
            assert (stages[0] / "version").read_text() == "A"
        else:
            assert not stages
        reaper.join(timeout=10)
        assert not reaper.is_alive()
        assert child.returncode == 0, child.stderr.read()
        evidence = json.loads((directory / "runtime-result.json").read_text())
        assert evidence == ["socket_closed", "executor_closed", "journal_closed"]
    finally:
        reaper.join(timeout=10)
        assert not reaper.is_alive()
        child.stdout.close()
        child.stderr.close()


@pytest.mark.parametrize(
    "case", ["ready", "wrong-version", "missing", "modified-agent", "real-runtime"]
)
def test_fixed_registered_ready_after_handoff(environment, tmp_path, case):
    import socket
    import threading

    management, home, directory = environment
    if case == "modified-agent":
        from device_runtime.client_macos_layout import MacClientLayout

        MacClientLayout(os.getuid(), home).plist.write_bytes(b"invalid")
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "registered-ready.m"
    harness.write_text(
        "#define getpwnam test_getpwnam\n#define INSTALL_PREFLIGHT_LIBRARY\n"
        f'#include "{native / "client-macos-install-preflight.m"}"\n'
        f'#include "{native / "client-macos-lease-request.m"}"\n'
        "#define RESOURCE_CHECK_LIBRARY\n"
        "static int verify_resource_tree(const char *p) {(void)p;return 0;}\n"
        f'#include "{native / "client-macos-recovery-check.h"}"\n'
        f'#include "{native / "client-macos-ready-request.h"}"\n'
        f'#include "{native / "client-macos-start-handoff.h"}"\n'
        f'#include "{native / "client-macos-registered-exit.h"}"\n'
        "#include <poll.h>\n"
        "struct passwd *test_getpwnam(const char *name) {\n"
        ' static struct passwd p;p.pw_name="test";p.pw_uid=getuid();p.pw_gid=getgid();\n'
        ' p.pw_dir=getenv("TEST_HOME");p.pw_shell="/bin/zsh";return strcmp(name,"test")?NULL:&p;}\n'
        "int main(int argc,char **argv) {@autoreleasepool {\n"
        " (void)registered_target_exact;(void)lease_request_connected;(void)lease_wait_original_exit;\n"
        " if(argc!=2)return 64;struct managed_guards g;\n"
        " NSDictionary *paths=prepare_registered(argv[1],getuid(),&g);if(!paths)return 70;\n"
        " if(!handoff_registered_start(argv[1],getuid(),paths,&g)) {\n"
        ' if(getenv("EXPECT_BAD_AGENT")) {release_guards(&g);puts("0");return 0;} return 71;}\n'
        ' NSString *version=@"B",*build=@"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";\n'
        ' if(getenv("REAL_RUNTIME")) {puts("HANDED_OFF");fflush(stdout);\n'
        " struct pollfd input={0,POLLIN,0};char c;\n"
        " if(poll(&input,1,5000)<=0 || read(0,&c,1)!=1 || c!=120)return 73;\n"
        ' version=[NSString stringWithUTF8String:getenv("TARGET_VERSION")];\n'
        ' build=[NSString stringWithUTF8String:getenv("TARGET_BUILD")]; }\n'
        " int ready=query_registered_ready(argv[1],getuid(),paths,&g,version,build,lease_clock()+1);\n"
        ' if(getenv("REAL_RUNTIME")) {\n'
        ' g.start=acquire_private_lock([paths[@"directory"] fileSystemRepresentation],"start.lock",getuid());\n'
        " if(g.start<0 || exit_registered_original(argv[1],getuid(),paths,&g,lease_clock()+3)!=LEASE_ACCEPTED)return 74;}\n"
        " if(acquireManagement(argv[1],getuid())!=-2)return 72;\n"
        ' release_guards(&g);printf("%d\\n",ready);return 0;}}\n'
    )
    binary = tmp_path / "registered-ready"
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
    (directory / "state").mkdir(mode=0o700)
    if case == "real-runtime":
        import select
        import sys

        from test_client_macos_lease_process import RUNTIME_CHILD

        from device_runtime.version import runtime_version

        version = runtime_version()
        watcher = subprocess.Popen(
            [str(binary), str(management)],
            env={
                **os.environ,
                "TEST_HOME": str(home),
                "REAL_RUNTIME": "1",
                "TARGET_VERSION": version["version"],
                "TARGET_BUILD": version["build_id"],
            },
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        child = None
        reaper = None
        try:
            assert select.select([watcher.stdout], [], [], 5)[0]
            assert watcher.stdout.readline().strip() == b"HANDED_OFF"
            child_source = RUNTIME_CHILD.replace("state_dir=root,", "state_dir=root/'state',")
            child = subprocess.Popen(
                [sys.executable, "-c", child_source, str(directory)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            reaper = threading.Thread(target=child.wait)
            reaper.start()
            assert select.select([child.stdout], [], [], 5)[0]
            line = child.stdout.readline()
            assert line, child.stderr.read()
            assert json.loads(line)["pid"] == child.pid
            watcher.stdin.write(b"x")
            watcher.stdin.flush()
            stdout, stderr = watcher.communicate(timeout=6)
            assert watcher.returncode == 0, stderr
            assert stdout.strip() == b"1"
            reaper.join(timeout=10)
            assert not reaper.is_alive()
            assert child.returncode == 0, child.stderr.read()
        finally:
            watcher.communicate(timeout=6)
            if reaper:
                reaper.join(timeout=10)
                assert not reaper.is_alive()
                child.stdout.close()
                child.stderr.close()
        return
    failures = []
    with socket.socket(socket.AF_UNIX) as listener:
        thread = None
        if case not in ("missing", "modified-agent"):
            listener.bind(str(directory / "state/maintenance.sock"))
            listener.listen(2)
            listener.settimeout(3)

            def serve():
                try:
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(2)
                        stream = connection.makefile("rb", buffering=0)
                        try:
                            request = json.loads(stream.readline())
                            assert request["action"] == "status"
                            response = {
                                "ready": True,
                                "pid": os.getpid(),
                                "upgrade_id": request["upgrade_id"],
                                "runtime_version": {
                                    "version": "B" if case == "ready" else "A",
                                    "build_id": "sha256:" + "a" * 64,
                                },
                            }
                            connection.sendall(json.dumps(response).encode() + b"\n")
                        finally:
                            stream.close()
                except (OSError, ValueError, AssertionError) as error:
                    failures.append(error)

            thread = threading.Thread(target=serve)
            thread.start()
        try:
            result = subprocess.run(
                [str(binary), str(management)],
                env={
                    **os.environ,
                    "TEST_HOME": str(home),
                    **({"EXPECT_BAD_AGENT": "1"} if case == "modified-agent" else {}),
                },
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
        finally:
            if thread:
                thread.join(timeout=4)
                assert not thread.is_alive()
        assert not failures
        assert result.stdout.strip() == ("1" if case == "ready" else "0")
        if case not in ("missing", "modified-agent"):
            listener.setblocking(False)
            with pytest.raises(BlockingIOError):
                listener.accept()


@pytest.mark.parametrize(
    "case",
    [
        "xml",
        "binary",
        "label",
        "args",
        "bool",
        "float",
        "extra",
        "permissions",
        "oversize",
        "symlink",
    ],
)
def test_native_exact_agent_disk_definition(environment, tmp_path, case):
    import plistlib

    from device_runtime.client_macos_layout import MacClientLayout

    management, home, _ = environment
    layout = MacClientLayout(os.getuid(), home)
    layout.plist.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    definition = layout.definition()
    if case == "label":
        definition["Label"] = "other"
    elif case == "args":
        definition["ProgramArguments"].append("--extra")
    elif case == "bool":
        definition["RunAtLoad"] = 1
    elif case == "float":
        definition["ThrottleInterval"] = 30.0
    elif case == "extra":
        definition["extra"] = True
    data = plistlib.dumps(
        definition, fmt=plistlib.FMT_BINARY if case == "binary" else plistlib.FMT_XML
    )
    layout.plist.write_bytes(b"x" * 65537 if case == "oversize" else data)
    layout.plist.chmod(0o644 if case == "permissions" else 0o600)
    if case == "symlink":
        # Keep original file, select a linked path through test-only managedPaths.
        linked = tmp_path / "agent-link"
        linked.symlink_to(layout.plist)
        plist_path = linked
    else:
        plist_path = layout.plist
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "agent.m"
    harness.write_text(
        "#define getpwnam test_getpwnam\n#define IDENTITY_LIBRARY\n"
        f'#include "{native / "client-macos-identity.m"}"\n'
        f'#include "{native / "client-macos-agent-check.h"}"\n'
        "struct passwd *test_getpwnam(const char *name) {\n"
        ' static struct passwd p;p.pw_name="test";p.pw_uid=getuid();p.pw_gid=getgid();\n'
        ' p.pw_dir=getenv("TEST_HOME");p.pw_shell="/bin/zsh";return strcmp(name,"test")?NULL:&p;}\n'
        "int main(int argc,char **argv) {@autoreleasepool {\n"
        " (void)acquireManagement;if(argc!=3)return 64;\n"
        " NSDictionary *identity=readIdentity(argv[1],getuid());if(!identity)return 70;\n"
        " NSMutableDictionary *paths=[managedPaths(identity) mutableCopy];\n"
        ' paths[@"plist"]=[NSString stringWithUTF8String:argv[2]];\n'
        ' printf("%d\\n",validate_registered_agent(identity,paths));return 0;}}\n'
    )
    binary = tmp_path / "agent"
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
    result = subprocess.run(
        [str(binary), str(management), str(plist_path)],
        env={**os.environ, "TEST_HOME": str(home)},
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.stdout.strip() == ("1" if case in ("xml", "binary") else "0")
    assert layout.plist.read_bytes() == (b"x" * 65537 if case == "oversize" else data)


@pytest.mark.parametrize(
    "case",
    [
        "first",
        "existing",
        "observe-exact",
        "observe-different",
        "observe-late-conflict",
        "observe-late-registration",
        "observe-missing",
        "observe-symlink",
        "observe-writable",
        "observe-late-content",
        "observe-late-inode",
        "corrupt-registration",
        "conflict",
        "late-conflict",
        "late-registration",
        "no-adapter",
        "race-directory",
        "race-file",
        "race-symlink",
    ],
)
def test_first_program_publication_requires_explicit_absence_and_conflict_gate(tmp_path, case):
    import importlib.util

    management = tmp_path / "management"
    management.mkdir(mode=0o700)
    (management / "management.lock").touch(mode=0o600)
    if case == "corrupt-registration":
        (management / "registration.json").write_text("invalid")
        (management / "registration.json").chmod(0o600)
    source = tmp_path / "resources/client-tree"
    source.mkdir(parents=True, mode=0o700)
    (source / "version").write_text("B")
    target = tmp_path / "program"
    if case == "observe-symlink":
        target.symlink_to(source, target_is_directory=True)
    elif case == "existing" or (case.startswith("observe-") and case != "observe-missing"):
        target.mkdir(mode=0o700)
        (target / "version").write_text("A" if case in ("existing", "observe-different") else "B")
        if case == "observe-writable":
            target.chmod(0o770)
    target_inode = target.lstat().st_ino if target.exists() else None
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("first_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), tmp_path, build)
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "first.m"
    harness.write_text(
        "#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n"
        "#include <sys/stat.h>\n#include <fcntl.h>\n#include <unistd.h>\n"
        "static int injected_rename(const char *from,const char *to,unsigned int flags) {\n"
        ' const char *mode=getenv("FIRST_RACE");\n'
        ' if(mode && !strncmp(mode,"race-",5)) {\n'
        " if(flags!=RENAME_EXCL)return -1;\n"
        ' if(!strcmp(mode,"race-directory")) {if(mkdir(to,0700))return -1;}\n'
        ' else if(!strcmp(mode,"race-file")) {int f=open(to,O_CREAT|O_EXCL|O_WRONLY,0600);\n'
        ' if(f<0)return -1;if(write(f,"A",1)!=1){close(f);return -1;}close(f);}\n'
        ' else if(symlink("missing-sentinel",to))return -1;\n'
        " }return renamex_np(from,to,flags); }\n"
        "#define renamex_np injected_rename\n"
        "#define main offline_writer_main\n#define EMBEDDED_RESOURCE_CHECK\n"
        f'#include "{native / "client-macos-writer-probe.c"}"\n'
        "#undef main\n#define IDENTITY_LIBRARY\n"
        f'#include "{native / "client-macos-identity.m"}"\n'
        f'#include "{native / "client-macos-bound-resources.h"}"\n'
        f'#include "{native / "client-macos-first-publish.h"}"\n'
        "struct probe {const char *management;const char *mode;int calls;};\n"
        "static int conflicts(void *opaque) {struct probe *p=opaque;p->calls++;\n"
        ' if(!strcmp(p->mode,"conflict") || ((!strcmp(p->mode,"late-conflict") || !strcmp(p->mode,"observe-late-conflict")) && p->calls==2))return 0;\n'
        ' if((!strcmp(p->mode,"late-registration") || !strcmp(p->mode,"observe-late-registration")) && p->calls==2) {\n'
        ' NSString *record=[[NSString stringWithUTF8String:p->management] stringByAppendingPathComponent:@"registration.json"];\n'
        ' int f=open([record fileSystemRepresentation],O_CREAT|O_EXCL|O_WRONLY,0600);if(f<0)return 0;write(f,"invalid",7);close(f);}\n'
        ' if(p->calls==2 && (!strcmp(p->mode,"observe-late-content") || !strcmp(p->mode,"observe-late-inode"))) {\n'
        ' NSString *target=[[[NSString stringWithUTF8String:p->management] stringByDeletingLastPathComponent] stringByAppendingPathComponent:@"program"];\n'
        ' if(!strcmp(p->mode,"observe-late-inode")) {\n'
        ' NSString *old=[target stringByAppendingString:@"-retained"];\n'
        " if(rename([target fileSystemRepresentation],[old fileSystemRepresentation]) || mkdir([target fileSystemRepresentation],0700))return 0;}\n"
        ' NSString *file=[target stringByAppendingPathComponent:@"version"];\n'
        " int f=open([file fileSystemRepresentation],O_WRONLY|O_CREAT|O_TRUNC,0644);if(f<0)return 0;\n"
        ' const char *v=!strcmp(p->mode,"observe-late-content")?"C":"B";if(write(f,v,1)!=1){close(f);return 0;}close(f);}\n'
        " return 1;}\n"
        "int main(int argc,char **argv) {@autoreleasepool {\n"
        " (void)managedPaths;if(argc!=5)return 64;int fd=acquireManagement(argv[1],getuid());if(fd<0)return 70;\n"
        " struct probe probe={argv[1],argv[3],0};\n"
        ' int result=(!strncmp(argv[3],"observe-",8)?observe_unregistered_program:publish_first_program)(argv[1],getuid(),fd,argv[2],argv[4],\n'
        ' !strcmp(argv[3],"no-adapter")?NULL:conflicts,&probe);\n'
        " if(acquireManagement(argv[1],getuid())!=-2)return 71;\n"
        ' close(fd);printf("%d\\n",result);return 0;}}\n'
    )
    binary = tmp_path / "first"
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
        [str(binary), str(management), str(target), case, str(source)],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
        env={**os.environ, "FIRST_RACE": case},
    )
    assert int(result.stdout) == (0 if case in ("first", "observe-exact") else 70)
    if case.startswith("observe-"):
        if case == "observe-missing":
            assert not target.exists()
        elif case == "observe-late-inode":
            assert target.stat().st_ino != target_inode
            assert (tmp_path / "program-retained").stat().st_ino == target_inode
            assert (target / "version").read_text() == "B"
        else:
            assert target.lstat().st_ino == target_inode
            expected = (
                "A"
                if case == "observe-different"
                else "C"
                if case == "observe-late-content"
                else "B"
            )
            assert (target / "version").read_text() == expected
            if case == "observe-symlink":
                assert target.is_symlink()
            if case == "observe-writable":
                assert target.stat().st_mode & 0o020
        assert (source / "version").read_text() == "B"
        assert not list(tmp_path.glob(".ai-device-client-stage-*"))
    elif case in ("first", "existing"):
        assert (target / "version").read_text() == ("B" if case == "first" else "A")
    elif case == "race-directory":
        assert target.is_dir() and not list(target.iterdir())
    elif case == "race-file":
        assert target.read_text() == "A"
    elif case == "race-symlink":
        assert target.is_symlink() and os.readlink(target) == "missing-sentinel"
    else:
        assert not target.exists()
    stages = list(tmp_path.glob(".ai-device-client-stage-*"))
    assert len(stages) == (
        1
        if case
        in ("late-conflict", "late-registration", "race-directory", "race-file", "race-symlink")
        else 0
    )
    assert (management / "registration.json").exists() == (
        case in ("corrupt-registration", "late-registration", "observe-late-registration")
    )


def prepare_bound_launcher_tree(source, package, launcher):
    """Build final test source before identity/inventory; no production overrides."""
    import shutil

    original = Path(os.environ["AI_DEVICE_TEST_MAC_TREE"])
    shutil.copytree(
        original,
        source,
        dirs_exist_ok=True,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "client-identity.json"),
    )
    shutil.copytree(
        Path(__file__).parents[1] / "src/device_runtime",
        package,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "client-identity.json"),
    )
    shutil.copy2(launcher, source / "bin/ai-device-client-launcher")
    initializer = (package / "__init__.py").read_text()
    (package / "__init__.py").write_text(
        initializer
        + r"""
import asyncio, atexit, fcntl, json, os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from . import client_macos_account, client_config, executor, client
root=Path(os.environ["TEST_HOME"])/"Library/Application Support/ai-device-client"
events=[]
assert os.get_inheritable(3)
def locked():
    assert not os.get_inheritable(3)
    with (root/"runtime.lock").open("r+") as stream:
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: pass
        else: raise AssertionError("runtime exclusion lost")
account=SimpleNamespace(layout=SimpleNamespace(directory=root,config=root/"client.json",uid=os.getuid()))
client_macos_account.resolve_account=lambda name: account
def load(path):
    assert path==root/"client.json"
    locked()
    return SimpleNamespace(server_url="wss://isolated.invalid",device_id="isolated",
        state_dir=root/"state",maintenance=True,max_active_tasks=1,
        max_concurrent_requests=1,max_concurrent_queries=1), "test-only-token-0000000000"
client_config.load=load
class Executor:
    capabilities=[]
    def __init__(self, *args, **kwargs): self.tasks=self
    async def active_count(self): return 0
    async def aclose(self):
        locked();await asyncio.sleep(.01);locked();events.append("executor_closed")
executor.LocalExecutor=Executor
class Journal:
    def __init__(self, *args, **kwargs): pass
    async def initialize(self): locked()
    async def unconfirmed(self, after): return []
    async def disconnect(self, device_id): locked();events.append("disconnected")
    async def close(self): locked();events.append("journal_closed")
client.Journal=Journal
class Socket:
    async def send(self, message): locked()
    async def recv(self):
        return json.dumps({"v":1,"type":"welcome","device_id":"isolated"})
    def __aiter__(self): return self
    async def __anext__(self):
        locked()
        from .version import runtime_version
        print(json.dumps({"pid":os.getpid(),"package":__file__,"identity":runtime_version()}),flush=True)
        # Finite test only: watchdog cancellation, never signal a client.
        asyncio.get_running_loop().call_later(12, asyncio.current_task().cancel)
        await asyncio.Event().wait()
    async def close(self, **kwargs): locked()
@asynccontextmanager
async def connect(*args, **kwargs):
    try: yield Socket()
    finally: locked();events.append("connection_closed")
client.connect=connect
def finished():
    try: os.fstat(3)
    except OSError: events.append("fd-closed")
    else: events.append("fd-leaked")
    (root/"bound-launcher-result.json").write_text(json.dumps(events))
atexit.register(finished)
"""
    )


def prepare_bound_start_helper(package, management):
    """Same inventoried source serves helper and client, no stdout handshake."""
    import textwrap

    original = (package / "__init__.py").read_text()
    helper = r"""
import time
from pathlib import Path
from types import SimpleNamespace
from . import client_macos_management as management_module, client_macos_start as start
from .client_macos_management import management_lock_matches
root=Path(TEST_MANAGEMENT)
owner=os.getuid()
management_module.MANAGEMENT_DIRECTORY=root
management_module.management_lock_matches=lambda fd,path,owner_uid: management_lock_matches(fd,path,owner_uid=owner)
os.getuid=lambda: 0
os.geteuid=lambda: 0
account=SimpleNamespace(layout=SimpleNamespace(uid=501))
start.registered_account=lambda: account
start.validate_agent=lambda account: None
start.observe_service=lambda *a,**k: SimpleNamespace(target="gui/501/local.ai-device.client")
def action(argv,**kwargs):
    import subprocess
    assert argv==["/bin/launchctl","kickstart","gui/501/local.ai-device.client"]
    assert not os.get_inheritable(4)
    assert management_lock_matches(4,root,owner_uid=owner)
    (root/"helper-request").write_text(str(os.getpid()))
    deadline=time.monotonic()+5
    while not (root/"helper-ack").exists():
        if time.monotonic()>=deadline: raise RuntimeError("finite helper handshake")
        time.sleep(.01)
    return subprocess.CompletedProcess(argv,0,b"")
start.run_kickstart=action
""".replace("TEST_MANAGEMENT", repr(str(management)))
    (package / "__init__.py").write_text(
        '__version__ = "0.3.4"\nimport os\n'
        "try:\n    os.fstat(4)\n    helper=True\nexcept OSError:\n    helper=False\n"
        + "if helper:\n"
        + textwrap.indent(helper, "    ")
        + "\nelse:\n"
        + textwrap.indent(original, "    ")
    )
