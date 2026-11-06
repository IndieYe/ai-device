"""Coordinator ordering/short circuit only; leaf primitives substituted."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Foundation compiler")


@pytest.mark.parametrize("failure", range(17))
def test_upgrade_once_order(tmp_path, failure):
    native = Path(__file__).parents[1] / "native"
    build_id = "invalid" if failure == 13 else "sha256:" + "a" * 64
    bound = failure >= 14
    expected_steps = (
        18
        if failure == 14
        else 2
        if bound
        else 0
        if failure == 13
        else 16
        if failure == 0
        else failure
    )
    identity_literal = (
        '@{@"version":@"bound-2",@"build_id":@"sha256:' + "b" * 64 + '"}'
        if failure == 14
        else '@{@"version":@"bound-2",@"build_id":@"bad"}'
        if failure == 16
        else "nil"
    )
    call = (
        'upgrade_registered_bound_once("management",0,@{},&g,10,start,NULL)'
        if bound
        else f'upgrade_registered_once("management",0,@{{}},&g,@"1",@"{build_id}",10,start,NULL)'
    )
    harness = tmp_path / "upgrade.m"
    harness.write_text(
        "#import <Foundation/Foundation.h>\n#include <limits.h>\n#include <math.h>\n#include <stdio.h>\n#include <unistd.h>\n"
        "struct managed_guards {int management,start,runtime;};\n"
        f"static int failure={failure if not bound else 0},step=0,bound={int(bound)};\n"
        "static int gate(void){return ++step!=failure;}\n"
        "static double lease_clock(void){return 0;}\n"
        "#define LEASE_ACCEPTED 1\n#define LEASE_BUSY 0\n"
        "static int registered_exit_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;return g->management==1 && g->start==2 && g->runtime==-1 && gate();}\n"
        'static int locate_bound_resources(char *p,uid_t u){(void)u;strcpy(p,"fixed");return gate();}\n'
        "static int verify_resource_tree(const char *p){(void)p;return gate();}\n"
        "static int tree_owner_valid(const char *p,uid_t u,int d){(void)p;(void)u;(void)d;return gate();}\n"
        "static int exit_registered_original(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,double d){(void)m;(void)u;(void)p;(void)d;if(!gate())return 0;g->runtime=3;return 1;}\n"
        "static int registered_guards_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;return g->management==1 && g->start==2 && g->runtime==3 && gate();}\n"
        "static int publish_registered_target(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,const char *s){(void)m;(void)u;(void)p;(void)g;(void)s;return gate()?0:70;}\n"
        "static int registered_target_exact(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return gate();}\n"
        "static int unload_registered_service_relay(const char *m,uid_t u,int fd,double d){(void)m;(void)u;(void)fd;(void)d;return gate();}\n"
        "static int load_registered_service_relay(const char *m,uid_t u,int fd,double d){(void)m;(void)u;(void)fd;(void)d;return gate();}\n"
        "static int registered_management_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;return g->management==1 && gate();}\n"
        "static int registered_management_confirmed(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}\n"
                "static int handoff_registered_start(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;if(!gate())return 0;if(!registered_guards_current(m,u,p,g))return 0;g->start=-1;g->runtime=-1;return 1;}\n"
        "static int start(void *p){(void)p;return gate();}\n"
        'static int query_registered_ready(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,NSString *v,NSString *b,double d){(void)d;if(g->start>=0 || g->runtime>=0)return 0;if(!registered_management_current(m,u,p,g))return 0;if(bound && (![v isEqual:@"bound-2"] || ![b isEqual:@"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]))return 0;return gate();}\n'
        f'static NSDictionary *read_target_identity(const char *s,uid_t u){{if(strcmp(s,"fixed") || u!=0)return nil;return {identity_literal};}}\n'
        f'#include "{native / "client-macos-registered-upgrade.h"}"\n'
        "int main(void){@autoreleasepool {(void)upgrade_registered_bound_once;(void)upgrade_recover_after_stage;struct managed_guards g={1,2,-1};\n"
        f"int result={call};\n"
        f"if(step!={expected_steps} || g.management!=1)return 4;\n"
        "if(failure && failure<12 && g.start!=2)return 5;\n"
        'printf("%d %d\\n",result,step);return 0;}}\n'
    )
    binary = tmp_path / "upgrade"
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
    result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=3)
    # Stage code per actual stopping point. The whole point of the change is that
    # a step with a real side effect (exit lease, unload, publish) reports an
    # UNKNOWN stage instead of pretending it was a clean refusal.
    code, steps = (int(x) for x in result.stdout.split())
    assert steps == expected_steps
    # The stopping point decides the reported stage, and stages with an attempted
    # side effect must not be reported as a clean refusal.
    expected_code = {
        0: 0,   # OK
        14: 0,  # OK via the bound-identity path
        1: 1,   # REFUSED: exit-guard identity invalid
        2: 1,   # REFUSED: bound resources unlocatable
        3: 1,   # REFUSED: resource tree unverifiable
        4: 1,   # REFUSED: tree ownership invalid
        13: 1,  # REFUSED: invalid build id rejected before anything ran
        15: 1,  # REFUSED (bound): identity unreadable
        16: 1,  # REFUSED (bound): identity mismatch
        5: 1,   # REFUSED: instance busy, exit never requested, nothing swapped
        6: 2,   # EXIT_UNKNOWN: guards lost right after the lease
        7: 3,   # UNLOAD_UNKNOWN: bootout failed, job state not proven
        8: 4,   # AFTER_UNLOAD: unloaded, program still the old one
        9: 5,   # PUBLISH_UNKNOWN: publication attempted and failed
        10: 5,  # PUBLISH_UNKNOWN: published but not exact
        # The handoff failed. The program IS published, but start/runtime were not
        # released (both cases below leave start held). Reporting AFTER_HANDOFF
        # here would tell recovery "locks are free, safe to bootstrap" and let it
        # race a launcher blocked on start.lock.
        11: 9,  # HANDOFF_UNKNOWN: release step failed
        12: 9,  # HANDOFF_UNKNOWN: identity re-check after release failed
    }[failure]
    assert code == expected_code


BUSY_UNKNOWN_HARNESS = """
#import <Foundation/Foundation.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
struct managed_guards {int management,start,runtime;};
static double lease_clock(void){return 0;}
#define LEASE_ACCEPTED 1
#define LEASE_BUSY 0
#define LEASE_UNKNOWN -1
static int registered_exit_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}
static int locate_bound_resources(char *p,uid_t u){(void)u;strcpy(p,"fixed");return 1;}
static int verify_resource_tree(const char *p){(void)p;return 1;}
static int tree_owner_valid(const char *p,uid_t u,int d){(void)p;(void)u;(void)d;return 1;}
static int exit_registered_original(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,double d){(void)m;(void)u;(void)p;(void)g;(void)d;return LEASE_RESULT;}
static int registered_guards_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}
static int unload_registered_service_relay(const char *m,uid_t u,int fd,double d){(void)m;(void)u;(void)fd;(void)d;return 1;}
static int publish_registered_target(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,const char *s){(void)m;(void)u;(void)p;(void)g;(void)s;return 1;}
static int registered_target_exact(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}
static int query_registered_ready(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,NSString *v,NSString *b,double d){(void)m;(void)u;(void)p;(void)g;(void)v;(void)b;(void)d;return 1;}
static int handoff_registered_start(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}
static int load_registered_service_relay(const char *m,uid_t u,int fd,double d){(void)m;(void)u;(void)fd;(void)d;return 1;}
static int registered_management_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}
static int registered_management_confirmed(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}
static NSDictionary *read_target_identity(const char *s,uid_t u){(void)s;(void)u;return nil;}
static int start(void *p){(void)p;return 1;}
#include "NATIVE_HEADER"
int main(void){@autoreleasepool {
    struct managed_guards g={1,2,-1};
    NSString *build=@"BUILD_ID";
    int r=upgrade_registered_once("management",0,@{},&g,@"1",build,10,start,NULL);
    printf("%d\\n",r);return 0;}}
"""


def test_busy_and_unknown_exit_are_reported_differently(tmp_path):
    """Busy is a clean refusal; an unconfirmed exit request is not.

    Both used to collapse into EXIT_UNKNOWN because the coordinator only asked
    `!= LEASE_ACCEPTED`. The two answers carry different operator contracts:
    REFUSED guarantees nothing was requested and may be retried, while
    EXIT_UNKNOWN means an exit was requested and its outcome is unconfirmed, so
    replaying is forbidden. Reporting a busy instance as EXIT_UNKNOWN told the
    caller a side effect might have happened when none did.
    """
    native = Path(__file__).parents[1] / "native"
    results = {}
    for name, lease in (("busy", "LEASE_BUSY"), ("unknown", "LEASE_UNKNOWN")):
        harness = tmp_path / f"{name}.m"
        harness.write_text(
            BUSY_UNKNOWN_HARNESS.replace("LEASE_RESULT", lease)
            .replace("NATIVE_HEADER", str(native / "client-macos-registered-upgrade.h"))
            .replace("BUILD_ID", "sha256:" + "a" * 64)
        )
        binary = tmp_path / name
        subprocess.run(
            ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-fobjc-arc",
             "-framework", "Foundation", str(harness), "-o", str(binary)],
            check=True, capture_output=True,
        )
        out = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=5)
        results[name] = int(out.stdout.split()[0])
    assert results["busy"] == 1, "busy must be a clean refusal"
    assert results["unknown"] == 2, "unconfirmed exit must stay unknown"


def test_real_upgrade_translation_unit_compiles(tmp_path):
    import importlib.util

    native = Path(__file__).parents[1] / "native"
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("upgrade_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = tmp_path / "source"
    source.mkdir()
    (source / "version").write_text("compile-only")
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), tmp_path, build)
    harness = tmp_path / "real-upgrade.m"
    includes = [
        "client-macos-install-preflight.m",
        "client-macos-recovery-check.h",
        "client-macos-bound-resources.h",
        "client-macos-registered-publish.h",
        "client-macos-lease-request.m",
        "client-macos-ready-request.h",
        "client-macos-registered-exit.h",
        "client-macos-start-handoff.h",
        "client-macos-service-relay.h",
        "client-macos-target-identity.h",
        "client-macos-registered-upgrade.h",
    ]
    harness.write_text(
        "#define main offline_writer_main\n#define EMBEDDED_RESOURCE_CHECK\n"
        f'#include "{native / "client-macos-writer-probe.c"}"\n#undef main\n'
        "#define INSTALL_PREFLIGHT_LIBRARY\n"
        + "".join(f'#include "{native / name}"\n' for name in includes)
        + "int main(void) {(void)upgrade_registered_once;(void)upgrade_registered_bound_once;(void)upgrade_recover_after_stage;(void)prepare_registered;"
        "(void)handoff_registered_start;(void)request_bound_registered_start;"
        "(void)unload_registered_service_relay;(void)load_registered_service_relay;return 0;}\n"
    )
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
            str(tmp_path / "upgrade-not-executed"),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    "failure_call,expected_actions",
    [(1, 0), (2, 0), (3, 1), (4, 2), (5, 3), (6, 5), (7, 5)],
)
@pytest.mark.parametrize("invalid", ["-1", "NAN"])
def test_upgrade_invalid_clock_stops_before_next_action(
    tmp_path, failure_call, expected_actions, invalid
):
    native = Path(__file__).parents[1] / "native"
    source = tmp_path / "clock-upgrade.m"
    source.write_text(
        "#import <Foundation/Foundation.h>\n#include <limits.h>\n#include <math.h>\n#include <string.h>\n"
        "struct managed_guards {int management,start,runtime;};\n"
        "static int clocks=0,actions=0;\n"
        f"static double lease_clock(void){{return ++clocks=={failure_call} ? {invalid} : 0;}}\n"
        "#define LEASE_ACCEPTED 1\n#define LEASE_BUSY 0\n"
        "static int registered_exit_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}\n"
        'static int locate_bound_resources(char *s,uid_t u){(void)u;strcpy(s,"fixed");return 1;}\n'
        "static int verify_resource_tree(const char *s){(void)s;return 1;}\n"
        "static int tree_owner_valid(const char *s,uid_t u,int d){(void)s;(void)u;(void)d;return 1;}\n"
        "static int exit_registered_original(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,double d){(void)m;(void)u;(void)p;(void)g;(void)d;actions++;return 1;}\n"
        "static int registered_guards_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}\n"
        "static int publish_registered_target(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,const char *s){(void)m;(void)u;(void)p;(void)g;(void)s;actions++;return 0;}\n"
        "static int registered_target_exact(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}\n"
        "static int registered_management_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}\n"
        "static int registered_management_confirmed(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}\n"
        "static int unload_registered_service_relay(const char *m,uid_t u,int fd,double d){(void)m;(void)u;(void)fd;(void)d;actions++;return 1;}\n"
        "static int load_registered_service_relay(const char *m,uid_t u,int fd,double d){(void)m;(void)u;(void)fd;(void)d;actions++;return 1;}\n"
        "static int start(void *p){(void)p;return 1;}\n"
        "static int handoff_registered_start(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;actions++;return 1;}\n"
        "static int query_registered_ready(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,NSString *v,NSString *b,double d){(void)m;(void)u;(void)p;(void)g;(void)v;(void)b;(void)d;return 1;}\n"
        "static NSDictionary *read_target_identity(const char *s,uid_t u){(void)s;(void)u;return nil;}\n"
        f'#include "{native / "client-macos-registered-upgrade.h"}"\n'
        "int main(void){@autoreleasepool {(void)upgrade_registered_bound_once;(void)upgrade_recover_after_stage;struct managed_guards g={1,2,-1};"
        'int r=upgrade_registered_once("unused",0,@{},&g,@"1",@"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",10,start,NULL);'
        # Any non-OK stage is a stop; OK(0) must never be reached here. The step
        # count is what proves nothing ran after the bad clock was observed.
        f"return r!=0 && actions=={expected_actions} && g.management==1 ? 0 : 1;}}}}\n"
    )
    binary = tmp_path / "clock-upgrade"
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
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run([str(binary)], check=True, capture_output=True, timeout=5)


RECOVERY_HARNESS = r"""
#import <Foundation/Foundation.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
struct managed_guards {int management,start,runtime;};
#define LEASE_ACCEPTED 1
#define LEASE_BUSY 0
/* Knobs for the two observables recovery must consult, plus call counters for
 * every observable side effect it could cause. */
static int confirmed_rc=1,guards_rc=1,handoff_rc=1,load_rc=1;
static int handoff_calls=0,load_calls=0;
static void unused_leaves(void);
static int registered_exit_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 0;}
static int locate_bound_resources(char *p,uid_t u){(void)u;strcpy(p,"fixed");return 0;}
static int verify_resource_tree(const char *p){(void)p;return 0;}
static int tree_owner_valid(const char *p,uid_t u,int d){(void)p;(void)u;(void)d;return 0;}
static int exit_registered_original(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,double d){(void)m;(void)u;(void)p;(void)g;(void)d;return 0;}
static int publish_registered_target(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,const char *s){(void)m;(void)u;(void)p;(void)g;(void)s;return 0;}
static int registered_target_exact(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 0;}
static int unload_registered_service_relay(const char *m,uid_t u,int fd,double d){(void)m;(void)u;(void)fd;(void)d;return 0;}
static int start(void *p){(void)p;return 1;}
static int query_registered_ready(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,NSString *v,NSString *b,double d){(void)m;(void)u;(void)p;(void)g;(void)v;(void)b;(void)d;return 0;}
static NSDictionary *read_target_identity(const char *s,uid_t u){(void)s;(void)u;return nil;}
static int registered_management_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 0;}
static int registered_management_confirmed(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return confirmed_rc;}
static int registered_guards_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return guards_rc;}
static int handoff_registered_start(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;handoff_calls++;if(!handoff_rc)return 0;g->start=-1;g->runtime=-1;return 1;}
static int load_registered_service_relay(const char *m,uid_t u,int fd,double d){(void)m;(void)u;(void)fd;(void)d;load_calls++;return load_rc;}
static double lease_clock(void){return 0;}
static void unused_leaves(void){(void)registered_exit_current;(void)locate_bound_resources;(void)verify_resource_tree;(void)tree_owner_valid;(void)exit_registered_original;(void)publish_registered_target;(void)registered_target_exact;(void)unload_registered_service_relay;(void)start;(void)query_registered_ready;(void)read_target_identity;(void)registered_management_current;}
"""




def _build_recovery_harness(tmp_path, stage, deadline="10", confirmed=1, guards=1, held_locks=False):
    native = Path(__file__).parents[1] / "native"
    source = tmp_path / "recover.m"
    # The starting descriptor state is a parameter because "what the stage code
    # claims" and "what this process actually holds" are different facts, and the
    # disagreement between them is what broke recovery.
    guards_literal = "{1,4,3}" if held_locks else "{1,-1,-1}"
    source.write_text(
        RECOVERY_HARNESS
        + '#include "' + str(native / "client-macos-registered-upgrade.h") + '"\n'
        + f"int main(void){{@autoreleasepool {{confirmed_rc={confirmed};guards_rc={guards};unused_leaves();"
        + "(void)upgrade_registered_once;(void)upgrade_registered_bound_once;"
        + f"struct managed_guards g={guards_literal};"
        + f"int r=upgrade_recover_after_stage(\"m\",0,@{{}},&g,{stage},{deadline});"
        + 'printf("%d %d %d %d\\n",r,handoff_calls,load_calls,g.start);return 0;}}\n'
    )
    binary = tmp_path / "recover"
    subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-fobjc-arc",
         "-framework", "Foundation", str(source), "-o", str(binary)],
        check=True, capture_output=True,
    )
    out = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=5)
    result, handoffs, loads, start_fd = (int(x) for x in out.stdout.split())
    return result, handoffs, loads, start_fd


def test_recovery_after_unload_hands_off_before_loading(tmp_path):
    """AFTER_UNLOAD still holds start/runtime; loading while holding them makes
    the launcher exit without exec. Recovery must hand off first, then load."""
    result, handoffs, loads, start_fd = _build_recovery_harness(tmp_path, stage=4)
    assert (result, handoffs, loads, start_fd) == (1, 1, 1, -1)


def test_recovery_after_handoff_never_reacquires_released_locks(tmp_path):
    """AFTER_HANDOFF already released start/runtime to the launcher. Re-taking or
    re-handing them off would be wrong, so neither may happen."""
    result, handoffs, loads, start_fd = _build_recovery_harness(tmp_path, stage=6)
    assert (result, handoffs, loads, start_fd) == (1, 0, 1, -1)


def test_recovery_after_handoff_refuses_when_locks_are_still_held(tmp_path):
    """The stage code is a claim, not evidence.

    A recorded AFTER_HANDOFF says start/runtime were released. If this process
    still holds either descriptor, the claim is wrong — an older build wrote it,
    or the handoff failed after publishing. Loading there would start a job whose
    launcher cannot take start.lock, which is exactly the deadlock the release
    order exists to prevent. The descriptor state must win over the stage code.
    """
    result, handoffs, loads, start_fd = _build_recovery_harness(
        tmp_path, stage=6, held_locks=True
    )
    assert (result, handoffs, loads, start_fd) == (0, 0, 0, 4)


def test_handoff_unknown_is_never_recovered(tmp_path):
    """HANDOFF_UNKNOWN means "published, but the release was not confirmed".

    Recovery must refuse it outright: the locks may still be held here. This is
    the stage the previous revision wrongly collapsed into AFTER_HANDOFF, which
    made an unconfirmed release look like a safe one.
    """
    result, handoffs, loads, start_fd = _build_recovery_harness(tmp_path, stage=9)
    # start_fd is -1: the harness starts with no held descriptors, and a refusal
    # must not change that. Nothing was handed off and nothing was loaded.
    assert (result, handoffs, loads, start_fd) == (0, 0, 0, -1)


@pytest.mark.parametrize(
    "stage,confirmed,guards,expected",
    [
        (4, 0, 1, (0, 0, 0)),  # unobservable management -> refuse, touch nothing
        (6, 0, 1, (0, 0, 0)),  # same for the post-publish stage
        (4, 1, 0, (0, 0, 0)),  # a launcher already raced into the window
    ],
)
def test_recovery_refuses_without_side_effects(tmp_path, stage, confirmed, guards, expected):
    """Every precondition failure must leave the device untouched: no handoff and
    no bootstrap. This is what makes "do not recover when the lock cannot be
    confirmed" a real property rather than a comment."""
    result, handoffs, loads, _ = _build_recovery_harness(
        tmp_path, stage=stage, confirmed=confirmed, guards=guards
    )
    assert (result, handoffs, loads) == expected


@pytest.mark.parametrize("stage", [0, 1, 2, 3, 5, 7, 8])
def test_recovery_refuses_every_non_recoverable_stage(tmp_path, stage):
    """Stages whose on-disk state is not certain (unknown side effects, or a job
    already loaded) must never be recovered."""
    result, handoffs, loads, _ = _build_recovery_harness(tmp_path, stage=stage)
    assert (result, handoffs, loads) == (0, 0, 0)
