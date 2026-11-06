"""Read-only recovery sequencing with leaf seams; never service operations."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Foundation")


@pytest.mark.parametrize(
    "case",
    [
        "ready",
        "not-ready",
        "expired",
        "guard-before",
        "guard-after",
        "source-bad",
        "target-bad",
        "identity",
        "start-held",
        "runtime-held",
        "query-expired",
        "tree-expired",
        "guard-expired",
    ],
)
def test_recovery_observation_no_replay(tmp_path, case):
    header = Path(__file__).parents[1] / "native/client-macos-recovery-observe.h"
    source = tmp_path / "observe.m"
    source.write_text(
        "#import <Foundation/Foundation.h>\n#include <math.h>\n#include <limits.h>\n#include <string.h>\n"
        "struct managed_guards {int start,runtime,management;};\n"
        f"static double clock_now={2 if case == 'expired' else 0};\n"
        "static double lease_clock(void) {return clock_now;}\n"
        "static int checks=0,queries=0;"
        "static int registered_management_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g) {"
        "(void)m;(void)u;(void)p;(void)g;checks++;"
        f"if(checks==3 && {int(case == 'guard-expired')})clock_now=2;"
        f"return {'0' if case == 'guard-before' else 'checks<3' if case == 'guard-after' else '1'};}}\n"
        'static int locate_bound_resources(char *out,uid_t u) {(void)u;strcpy(out,"source");return 1;}\n'
        "static int reads=0;static NSDictionary *read_target_identity(const char *p,uid_t u) {(void)u;reads++;"
        f"if(reads==3 && {int(case == 'tree-expired')})clock_now=2;"
        f'if(!strcmp(p,"source") && {int(case == "source-bad")})return nil;'
        f'if(!strcmp(p,"target") && {int(case == "target-bad")})return nil;'
        f'if(!strcmp(p,"target") && {int(case == "identity")})return @{{@"version":@"other"}};'
        'return @{@"version":@"1",@"build_id":@"build"};}\n'
        "static int query_registered_ready(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,NSString *v,NSString *b,double d) {"
        "(void)m;(void)u;(void)p;(void)g;(void)v;(void)b;(void)d;queries++;"
        f"if({int(case == 'query-expired')})clock_now=2;"
        f"return {int(case != 'not-ready')};}}\n"
        f'#include "{header}"\n'
        "int main(void) {@autoreleasepool {struct managed_guards g={"
        f"{8 if case == 'start-held' else -1},{9 if case == 'runtime-held' else -1},7}};"
        'NSDictionary *paths=@{@"program":@"target",@"management_owner":@0};'
        'int result=observe_registered_recovery("unused",0,paths,&g,1);'
        f"if(result!={2 if case == 'ready' else 1 if case == 'not-ready' else 0})return 1;"
        f"if(queries!={int(case in ('ready', 'not-ready', 'guard-after', 'query-expired', 'tree-expired', 'guard-expired'))})return 2;"
        "if(g.management!=7)return 3;return 0;}}\n"
    )
    binary = tmp_path / "observe"
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
    subprocess.run([str(binary)], check=True, capture_output=True, timeout=3)


@pytest.mark.parametrize("failure_call", [1, 2, 3, 4])
@pytest.mark.parametrize("invalid", ["-1", "NAN", "INFINITY"])
def test_recovery_invalid_clock_never_reports_exact(tmp_path, failure_call, invalid):
    header = Path(__file__).parents[1] / "native/client-macos-recovery-observe.h"
    source = tmp_path / "clock.m"
    source.write_text(
        "#import <Foundation/Foundation.h>\n#include <math.h>\n#include <limits.h>\n#include <string.h>\n"
        "struct managed_guards {int start,runtime,management;};\n"
        "static int calls=0;\n"
        f"static double lease_clock(void){{return ++calls=={failure_call} ? {invalid} : 0;}}\n"
        "static int registered_management_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g){(void)m;(void)u;(void)p;(void)g;return 1;}\n"
        'static int locate_bound_resources(char *s,uid_t u){(void)u;strcpy(s,"source");return 1;}\n'
        "static NSDictionary *read_target_identity(const char *s,uid_t u){(void)s;(void)u;return @{};}\n"
        "static int query_registered_ready(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,NSString *v,NSString *b,double d){(void)m;(void)u;(void)p;(void)g;(void)v;(void)b;(void)d;return 1;}\n"
        f'#include "{header}"\n'
        "int main(void){@autoreleasepool {struct managed_guards g={-1,-1,7};"
        'int r=observe_registered_recovery("unused",0,@{@"program":@"target",@"management_owner":@0},&g,1);'
        "return r==RECOVERY_UNKNOWN && g.management==7 ? 0 : 1;}}\n"
    )
    binary = tmp_path / "clock"
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
