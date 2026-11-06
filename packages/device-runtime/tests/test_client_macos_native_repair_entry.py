"""Damaged-program repair entry contract. Substitutes every leaf operation.

Guard model under test (enforced by the real leaf helpers, see
client-macos-recovery-check.h and client-macos-start-handoff.h):

    management + start            -> replacement forbidden
    start/runtime both released   -> the only legal state for a ready query
    management + start + runtime  -> publication allowed (instance absent)

The substitute guards are REAL file descriptors opened on /dev/null, not
invented integers. The entry closes its own start descriptor, so fake numbers
would hide (or fabricate) failures: an unopened descriptor makes that close
fail with EBADF and the entry would abort before the ready probe.
"""

import re
import subprocess
import sys
from pathlib import Path
from string import Template

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin compiler")

ARGS = ["repair-registered-program", "--confirm-service-not-running"]

COMMON = (
    "#import <Foundation/Foundation.h>\n"
    "#include <fcntl.h>\n"
    "#include <math.h>\n"
    "#include <stdio.h>\n"
    "#include <string.h>\n"
    "#include <unistd.h>\n"
    "struct managed_guards {int management,start,runtime;NSDictionary *__strong identity;};\n"
)

# The program path must exist as a real directory: repair refuses to create a
# target, so the harness needs a present one. Built per test from tmp_path.
def paths_literal(program):
    return (
        '@{@"directory":@"/tmp",@"runtime_owner":@0,@"program":@"'
        + program + '",@"management_owner":@0}'
    )

# One C source for the substitute set. string.Template is used instead of an
# f-string so the C braces stay literal.
BODY = Template(
    """
static int published=0, ready_calls=0, start_taken=0;
static int ready_violation=0, publish_violation=0;
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
static int clock_step=0;
static double lease_clock(void){
    /* Jump past the deadline after $expire_after reads so a phase recheck can
     * be exercised; negative means "never expire". */
    clock_step++;
    if ($expire_after >= 0 && clock_step > $expire_after) return 1000.0;
    return 1;}
static int locate_bound_resources(char *p,uid_t u){(void)u;strcpy(p,"/bound");return 1;}
static int verify_resource_tree(const char *p){(void)p;return 1;}
static int tree_owner_valid(const char *p,uid_t u,int d){(void)p;(void)u;(void)d;return 1;}
static NSDictionary *read_target_identity(const char *p,uid_t u){
    (void)p;(void)u;
    return @{@"version":@"1",@"build_id":@"sha256:abc"};}
static int query_registered_ready(const char *m,uid_t u,NSDictionary *p,
                                  struct managed_guards *g,NSString *v,
                                  NSString *b,double d){
    (void)m;(void)u;(void)p;(void)v;(void)b;(void)d;
    ready_calls++;
    if(g->start>=0||g->runtime>=0)ready_violation=1;
    return $serving;}
static int acquire_private_lock(const char *d,const char *n,uid_t o){
    (void)d;(void)n;(void)o;
    if ($start_lock_rc < 0) return $start_lock_rc;
    start_taken=1;return open_probe();}
static int acquire_registered_runtime(const char *m,uid_t u,NSDictionary *p,
                                      struct managed_guards *g){
    (void)m;(void)u;(void)p;
    $runtime_setup}
static int publish_registered_target(const char *m,uid_t u,NSDictionary *p,
                                     struct managed_guards *g,const char *s){
    (void)m;(void)u;(void)p;(void)s;
    if(g->management<0||g->start<0||g->runtime<0)publish_violation=1;
    published++;return $publish_rc;}
static int registered_target_exact(const char *m,uid_t u,NSDictionary *p,
                                   struct managed_guards *g){
    (void)m;(void)u;(void)p;
    return g->management>=0 && g->start>=0 && g->runtime>=0;}
static uid_t test_uid(void){return $root;}
static uid_t test_euid(void){return $root;}
#define getuid test_uid
#define geteuid test_euid
"""
)


def compile_harness(tmp_path, body, name):
    """Build the harness; the process exit status is the entry's return code."""
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / f"{name}.m"
    harness.write_text(
        COMMON
        + body
        + "\n"
        + f'#include "{native}/client-macos-repair-entry.h"\n'
        + "int main(int argc,char **argv){int r=repair_registered_entry(argc,argv);"
          'printf("%d %d %d %d %d %d\\n",r,published,ready_calls,start_taken,'
          'ready_violation,publish_violation);return r;}\n'
    )
    binary = tmp_path / name
    compiled = subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-fobjc-arc",
         "-framework", "Foundation", str(harness), "-o", str(binary)],
        check=False, capture_output=True, text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    return subprocess.run([str(binary), *ARGS], check=False,
                          capture_output=True, text=True, timeout=5)


def stats(result):
    """Return (entry_return, published, ready_calls, start_taken, violations)."""
    line = result.stdout.splitlines()[-1]
    fields = [int(v) for v in line.split()]
    assert len(fields) == 6, result.stdout
    return fields


def harness_body(*, registered=1, serving=0, runtime_ok=1, publish_ok=1, root=0,
                 program="/tmp/ai-device-repair-target", start_lock_rc=21,
                 expire_after=-1):
    """Build the substitute set; records guard state at every leaf boundary."""
    return BODY.substitute(
        paths=paths_literal(program),
        registered=registered,
        serving=serving,
        publish_rc=0 if publish_ok else 1,
        root=root,
        start_lock_rc=start_lock_rc,
        expire_after=expire_after,
        runtime_setup=(
            "g->runtime=open_probe();return g->runtime>=0;"
            if runtime_ok
            else "g->runtime=-2;return 0;"
        ),
    )


def make_target(tmp_path):
    """Create the program target that repair requires to already exist."""
    target = tmp_path / "program"
    target.mkdir(exist_ok=True)
    return str(target)


def test_success_path_takes_start_and_publishes_once(tmp_path):
    """Only success shape: ready queried with locks released, then swap."""
    result = compile_harness(
        tmp_path, harness_body(program=make_target(tmp_path)), "repair-success")
    assert stats(result) == [0, 1, 1, 1, 0, 0], result.stdout
    assert result.returncode == 0


def test_ready_is_queried_without_holding_start(tmp_path):
    """The ready probe must not run while start is held (else it always fails)."""
    result = compile_harness(
        tmp_path, harness_body(program=make_target(tmp_path)), "repair-ready-order")
    assert stats(result)[4] == 0, result.stdout  # ready_violation


def test_running_instance_blocks_repair_before_publication(tmp_path):
    """Held runtime.lock means a live instance; refuse and never publish."""
    result = compile_harness(
        tmp_path, harness_body(runtime_ok=0, program=make_target(tmp_path)), "repair-held")
    assert stats(result) == [70, 0, 1, 1, 0, 0], result.stdout


def test_present_instance_refuses_before_any_publication(tmp_path):
    """A serving instance refuses even when the caller asserted it was stopped."""
    result = compile_harness(
        tmp_path, harness_body(serving=1, program=make_target(tmp_path)), "repair-serving")
    assert stats(result) == [70, 0, 1, 0, 0, 0], result.stdout


def test_unregistered_target_is_refused(tmp_path):
    result = compile_harness(
        tmp_path, harness_body(registered=0, program=make_target(tmp_path)), "repair-unreg")
    assert stats(result) == [70, 0, 0, 0, 0, 0], result.stdout


def test_publication_failure_is_not_reported_as_success(tmp_path):
    result = compile_harness(
        tmp_path, harness_body(publish_ok=0, program=make_target(tmp_path)), "repair-pubfail")
    assert stats(result) == [70, 1, 1, 1, 0, 0], result.stdout


def test_arguments_and_identity_are_strict(tmp_path):
    """Bad argv or a non-root caller must not reach any leaf operation."""
    compile_harness(tmp_path, harness_body(program=make_target(tmp_path)), "repair-argv")
    binary = tmp_path / "repair-argv"

    for args in (
        ["repair-registered-program"],
        ["repair-registered-program", "--force"],
        ARGS + ["extra"],
    ):
        out = subprocess.run([str(binary), *args], check=False,
                             capture_output=True, text=True, timeout=3)
        assert out.returncode == 64, args
        fields = [int(v) for v in out.stdout.splitlines()[-1].split()]
        assert fields[0] == 64 and fields[1:3] == [0, 0], out.stdout

    # Non-root caller: refused before the guard work begins.
    nonroot = compile_harness(
        tmp_path, harness_body(root=501, program=make_target(tmp_path)), "repair-nonroot")
    assert nonroot.returncode == 70
    fields = [int(v) for v in nonroot.stdout.splitlines()[-1].split()]
    assert fields[0] == 70 and fields[1:3] == [0, 0], nonroot.stdout


def _strip_comments(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def test_absent_target_is_refused_not_created(tmp_path):
    """Registered identity does not imply the program tree exists.

    Regression: publish falls back to RENAME_EXCL on ENOENT, so without an
    explicit precondition repair would CREATE a tree and still print
    REPLACED. Repair is not first installation.
    """
    missing = tmp_path / "no-such-program"
    result = compile_harness(
        tmp_path, harness_body(program=str(missing)), "repair-absent")
    fields = stats(result)
    # Refused, nothing published, and the target must not appear.
    assert fields[0] == 70 and fields[1] == 0, result.stdout
    assert not missing.exists(), "repair created the target it was asked to replace"


def test_unusable_start_lock_is_not_reported_as_running(tmp_path):
    """Acquire -1 (unusable gate) must not be diagnosed as a live service."""
    result = compile_harness(
        tmp_path,
        harness_body(program=make_target(tmp_path), start_lock_rc=-1),
        "repair-gatebroken")
    fields = stats(result)
    assert fields[0] == 70 and fields[1] == 0, result.stdout
    # SERVICE_RUNNING would send the operator hunting for a process.
    assert "AI_DEVICE_REPAIR_REFUSED_SERVICE_RUNNING_V1" not in result.stdout
    assert "AI_DEVICE_REPAIR_UNKNOWN_OR_REFUSED_V1" in result.stdout


def test_busy_start_lock_is_still_reported_as_running(tmp_path):
    """The genuinely busy case must keep its specific diagnosis."""
    result = compile_harness(
        tmp_path,
        harness_body(program=make_target(tmp_path), start_lock_rc=-2),
        "repair-gatebusy")
    fields = stats(result)
    assert fields[0] == 70 and fields[1] == 0, result.stdout
    assert "AI_DEVICE_REPAIR_REFUSED_SERVICE_RUNNING_V1" in result.stdout


def test_expired_deadline_blocks_publication(tmp_path):
    """The 120s budget must gate side effects, not just admit the run.

    Regression: the deadline was checked once at entry, so a slow resource
    verification or ready probe could still be followed by a swap.
    """
    result = compile_harness(
        tmp_path,
        harness_body(program=make_target(tmp_path), expire_after=1),
        "repair-expired")
    fields = stats(result)
    assert fields[0] == 70, result.stdout
    assert fields[1] == 0, "published after the deadline expired"


def test_repair_never_addresses_config_or_business_state():
    """Repair replaces the program tree only; enforced statically.

    This is about which paths the entry may name at all, so a substitute could
    not demonstrate it. Comments are stripped first: prose that merely mentions
    a helper must not be mistaken for a call site.
    """
    native = Path(__file__).parents[1] / "native"
    source = _strip_comments((native / "client-macos-repair-entry.h").read_text())
    for forbidden in ('@"config"', '@"runtime_lock"', '"client.json"',
                      '@"start_gate"'):
        assert forbidden not in source, forbidden
    # No stop request either: repair must never ask an instance to exit.
    for forbidden in ("exit_registered_original", "lease_request_connected",
                      "unload_registered_service_relay", "load_registered_service_relay"):
        assert forbidden not in source, forbidden
    # Nor may it fall back to first-install publication.
    for forbidden in ("first_program", "first_install", "observe_unregistered"):
        assert forbidden not in source, forbidden
    # The runtime lock IS taken, strictly after the ready observation.
    ready_at = source.index("query_registered_ready")
    runtime_at = source.index("acquire_registered_runtime")
    assert ready_at < runtime_at, "runtime must be acquired after readiness"
    # And publication happens only after that acquisition.
    assert source.index("publish_registered_target") > runtime_at
