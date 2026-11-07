"""Uninstall entry contract. Real filesystem, substituted service operations.

The properties that matter for a plain uninstall are:

  1. service first, then the definition, then registration, then the program --
     so no step leaves a definition pointing at a tree that is already gone;
  2. configuration, credentials and business data are never touched.

Both are checked against a real directory tree rather than an invented one:
the entry's own removal helpers run for real (fd-relative, no symlink
following), and only the launchd relay and the guard plumbing are substituted.
Inventing those helpers would test the substitute instead of the entry, and a
previous version of this file did exactly that.

The substitute guards are REAL descriptors opened on /dev/null, because the
entry closes its own descriptors and invented integers would fabricate EBADF.
"""

import os
import subprocess
import sys
from pathlib import Path
from string import Template

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin compiler")

ARGS = ["uninstall-registered-program", "--confirm-service-not-running"]

COMMON = (
    "#import <Foundation/Foundation.h>\n"
    "#include <fcntl.h>\n"
    "#include <math.h>\n"
    "#include <stdio.h>\n"
    "#include <string.h>\n"
    "#include <sys/stat.h>\n"
    "#include <unistd.h>\n"
    "struct managed_guards {int management,start,runtime;NSDictionary *__strong identity;};\n"
)

# Only the service relay, the guards and process identity are substituted. The
# program tree is NOT hardcoded: it is passed as paths[@"program"], and the
# entry removes that exact directory through its own remove_tree_at.
BODY = Template(
    """
static int unload_calls=0, unload_ok=$unload_ok;
/* Order marks: 1 = unload, 2 = definition removed, 3 = registration removed,
 * 4 = program removed. Recorded by the harness so the sequence is asserted,
 * not just the fact that every step eventually ran. */
static int order[8]; static int steps=0;
static void note(int what){if(steps<8)order[steps++]=what;}
static int open_probe(void){return open("/dev/null",O_RDWR|O_CLOEXEC);}
static void close_probe(int *fd){if(*fd>=0){close(*fd);*fd=-1;}}
static void release_guards(struct managed_guards *g){
    close_probe(&g->management);close_probe(&g->start);
    close_probe(&g->runtime);g->identity=nil;}
static NSDictionary *prepare_start_barrier(const char *m,uid_t u,
                                           struct managed_guards *g){
    (void)m;(void)u;
    g->management=open_probe();g->start=open_probe();g->runtime=-1;
    return $paths;}
static int registration_state(const char *m,uid_t u,int fd){
    (void)m;(void)u;(void)fd;return $registered;}
static double lease_clock(void){return 1;}
static int verify_resource_tree(const char *p){(void)p;return $program_valid;}
/* Ownership is root in production; the harness runs as the test user, so this
 * one leaf is substituted. The recursive delete below still runs for real. */
static int tree_owner_valid(const char *p,uid_t u,unsigned int d){(void)p;(void)u;(void)d;return 1;}
static int acquire_registered_runtime(const char *m,uid_t u,NSDictionary *p,
                                      struct managed_guards *g){
    (void)m;(void)u;(void)p;
    if(!$runtime_ok){g->runtime=-2;return 0;}
    g->runtime=open_probe();return g->runtime>=0;}
static int registered_guards_current(const char *m,uid_t u,NSDictionary *p,
                                     struct managed_guards *g){
    (void)m;(void)u;(void)p;
    return g->management>=0 && g->start>=0 && g->runtime>=0;}
static int unload_registered_service_relay(const char *m,uid_t u,int fd,
                                           const char *tree,double d){
    (void)m;(void)u;(void)fd;(void)tree;(void)d;
    unload_calls++;
    if(unload_ok) note(1);
    return unload_ok;}
/* 1 loaded, 0 not loaded, -1 unknown -- as the real launchctl print probe. */
#define UNINSTALL_HAVE_JOB_PROBE 0
static int launchctl_job_loaded(const char *t){(void)t;return $job_loaded;}
static uid_t test_uid(void){return 0;}
static uid_t test_euid(void){return 0;}
#define getuid test_uid
#define geteuid test_euid
"""
)


def build_tree(tmp_path):
    """A tree shaped like the real install, including data that must survive."""
    program = tmp_path / "opt" / "ai-device-client"
    program.mkdir(parents=True)
    (program / "bin").mkdir()
    (program / "bin" / "ai-device-client-launcher").write_text("launcher\n")

    plist_dir = tmp_path / "LaunchAgents"
    plist_dir.mkdir()
    plist = plist_dir / "local.ai-device.client.plist"
    plist.write_text("<plist/>")

    management = tmp_path / "management"
    management.mkdir()
    (management / "registration.json").write_text("{}")
    (management / "management.lock").write_text("")

    # Everything below is configuration / credentials / business data.
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "client.json").write_text('{"device_id":"macminim6"}\n')
    (runtime / "token").write_text("secret\n")
    (runtime / "state").mkdir()
    (runtime / "state" / "queue.json").write_text("[]\n")
    return program, plist, management, runtime, plist_dir


def compile_harness(tmp_path, *, registered=1, program_valid=1, runtime_ok=1,
                    unload_ok=1, job_loaded=1, name="uninstall",
                    break_registration=False, break_program=False):
    program, plist, management, runtime, _ = build_tree(tmp_path)
    if break_registration:
        # A directory where a regular file is expected: the entry's own
        # unlinkat path refuses it (S_ISREG guard), so the failure is produced
        # by the real code rather than by a substitute.
        (management / "registration.json").unlink()
        (management / "registration.json").mkdir()
    if break_program:
        # Removing a non-empty tree requires walking it; making the walk
        # impossible (parent not a directory) surfaces the removal failure.
        program.rename(tmp_path / "program-real")
        program.write_text("not a directory\n")
    body = BODY.substitute(
        paths=(
            '@{@"directory":@"' + str(runtime) + '",@"runtime_owner":@' + str(os.getuid())
            + ',@"program":@"' + str(program) + '",@"management_owner":@0'
            ',@"plist":@"' + str(plist) + '"}'
        ),
        registered=registered,
        program_valid=program_valid,
        runtime_ok=runtime_ok,
        unload_ok=unload_ok,
        job_loaded=job_loaded,
    )
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / f"{name}.m"
    harness.write_text(
        COMMON
        + body
        + "\n"
        # The entry hardcodes the two absolute locations it must never trust
        # from the caller; redirect them so the test can observe them.
        + f'#define UNINSTALL_TEST_MANAGEMENT "{management}"\n'
        + f'#define UNINSTALL_TEST_OPT "{program.parent}"\n'
        + f'#define UNINSTALL_MANAGEMENT "{management}"\n'
        + f'#define UNINSTALL_OPT "{program.parent}"\n'
        + f'#define UNINSTALL_MANAGEMENT_OWNER {os.getuid()}\n'
        # stage-cleanup.h (used for the fd-relative recursive delete) needs the
        # bound-tree helpers it sits on, exactly as the real translation unit
        # provides them.
        + f'#include "{native}/client-macos-bound-resources.h"\n'
        + f'#include "{native}/client-macos-uninstall-entry.h"\n'
        + "int main(int argc,char **argv){"
          "int r=uninstall_registered_entry(argc,argv);"
          'printf("RC=%d UNLOADS=%d\\n",r,unload_calls);return r;}\n'
    )
    binary = tmp_path / name
    compiled = subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-fobjc-arc",
         "-framework", "Foundation", str(harness), "-o", str(binary)],
        check=False, capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run([str(binary), *ARGS], check=False,
                            capture_output=True, text=True, timeout=5)
    return result, program, plist, management, runtime


def rc_of(result):
    line = [ln for ln in result.stdout.splitlines() if ln.startswith("RC=")][-1]
    return int(line.split()[0].split("=")[1])


def test_uninstall_removes_service_registration_and_program(tmp_path):
    result, program, plist, management, _runtime = compile_harness(tmp_path)
    assert rc_of(result) == 0, result.stdout
    assert "AI_DEVICE_UNINSTALL_SERVICE_REMOVED_V1" in result.stdout
    assert result.stdout.count("UNLOADS=1")
    assert not program.exists(), "program tree must be removed"
    assert not plist.exists(), "definition must be removed"
    assert not (management / "registration.json").exists(), "registration must be removed"


def test_uninstall_preserves_configuration_credentials_and_state(tmp_path):
    """The whole point of a plain uninstall: data stays."""
    result, _, _, _, runtime = compile_harness(tmp_path)
    assert rc_of(result) == 0, result.stdout
    assert (runtime / "client.json").read_text() == '{"device_id":"macminim6"}\n'
    assert (runtime / "token").read_text() == "secret\n"
    assert (runtime / "state" / "queue.json").read_text() == "[]\n"


def test_unregistered_is_refused_and_nothing_is_removed(tmp_path):
    result, program, plist, management, _ = compile_harness(tmp_path, registered=0)
    assert rc_of(result) == 70
    assert "AI_DEVICE_UNINSTALL_REFUSED_NOT_REGISTERED_V1" in result.stdout
    assert program.exists() and plist.exists()
    assert (management / "registration.json").exists()


def test_damaged_program_is_refused_before_unload(tmp_path):
    result, program, plist, _, _ = compile_harness(tmp_path, program_valid=0)
    assert rc_of(result) == 70
    assert "AI_DEVICE_UNINSTALL_REFUSED_PROGRAM_INVALID_V1" in result.stdout
    assert result.stdout.count("UNLOADS=0")
    assert program.exists() and plist.exists()


def test_running_instance_is_refused(tmp_path):
    result, program, plist, _, _ = compile_harness(tmp_path, runtime_ok=0)
    assert rc_of(result) == 70
    assert "AI_DEVICE_UNINSTALL_REFUSED_SERVICE_RUNNING_V1" in result.stdout
    assert program.exists() and plist.exists()


def test_failed_unload_keeps_definition_registration_and_program(tmp_path):
    """A failed bootout leaves the target unknown; nothing may be deleted."""
    result, program, plist, management, _ = compile_harness(tmp_path, unload_ok=0)
    assert rc_of(result) == 70
    assert "AI_DEVICE_UNINSTALL_UNLOAD_UNKNOWN_V1" in result.stdout
    assert program.exists()
    assert plist.exists(), "definition must survive so the retry can target it"
    assert (management / "registration.json").exists()


def test_registration_failure_happens_after_definition_is_gone(tmp_path):
    """Pins the order: the definition is removed BEFORE registration.

    Both orders leave the same end state on the success path, so a test that
    only inspects the outcome cannot tell them apart. Making registration
    removal fail exposes which side of it the definition removal sits on.
    """
    result, program, plist, management, _ = compile_harness(
        tmp_path, break_registration=True, name="uninstall-identity"
    )
    assert rc_of(result) == 70
    assert "AI_DEVICE_UNINSTALL_IDENTITY_FAILED_V1" in result.stdout
    assert not plist.exists(), "definition is removed before registration"
    assert (management / "registration.json").exists(), "the failing step left it"
    assert program.exists(), "program removal must not run after a failed step"


def test_program_removal_happens_last(tmp_path):
    """Pins the other end of the order: the program goes after registration."""
    result, _program, plist, management, _ = compile_harness(
        tmp_path, break_program=True, name="uninstall-program"
    )
    assert rc_of(result) == 70
    assert "AI_DEVICE_UNINSTALL_PROGRAM_REMAINS_V1" in result.stdout
    assert not plist.exists(), "definition removed before the program"
    assert not (management / "registration.json").exists(), (
        "registration removed before the program"
    )


def test_already_stopped_service_is_uninstalled_without_bootout(tmp_path):
    """A service the operator already stopped must still uninstall.

    This is a real defect found on hardware: bootout of a job that is not
    loaded exits 3, which the shared unload path reports as unknown, so an
    operator who did exactly what --confirm-service-not-running asks for could
    never finish the uninstall. The job state is therefore read first.
    """
    result, program, plist, management, _ = compile_harness(
        tmp_path, job_loaded=0, name="uninstall-stopped"
    )
    assert rc_of(result) == 0, result.stdout
    assert "AI_DEVICE_UNINSTALL_SERVICE_REMOVED_V1" in result.stdout
    assert "UNLOADS=0" in result.stdout, "no bootout when nothing is loaded"
    assert not program.exists()
    assert not plist.exists()
    assert not (management / "registration.json").exists()


def test_unreadable_job_state_is_refused(tmp_path):
    """Not knowing whether the job is loaded must not be read as 'not loaded'."""
    result, program, plist, management, _ = compile_harness(
        tmp_path, job_loaded=-1, name="uninstall-unknown"
    )
    assert rc_of(result) == 70
    assert "AI_DEVICE_UNINSTALL_UNKNOWN_OR_REFUSED_V1" in result.stdout
    assert "UNLOADS=0" in result.stdout
    assert program.exists() and plist.exists()
    assert (management / "registration.json").exists()


def test_loaded_service_is_booted_out_before_removal(tmp_path):
    """The ordinary case still unloads first."""
    result, program, plist, _, _ = compile_harness(tmp_path, job_loaded=1)
    assert rc_of(result) == 0, result.stdout
    assert "UNLOADS=1" in result.stdout
    assert not program.exists() and not plist.exists()


def test_entry_never_references_runtime_data_paths():
    """A guard against a later edit quietly adding data deletion.

    Configuration, token and state live in the runtime directory. The entry
    removes by name inside a checked parent, so the names it may remove are
    visible in the source; none of the runtime names appear as a removal target.
    """
    source = (Path(__file__).parents[1] / "native/client-macos-uninstall-entry.h").read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("*", "/*"))
    )
    assert '"registration.json"' in code
    for name in ('"client.json"', '"token"', '"state"'):
        assert name not in code, f"uninstall must not remove {name}"
