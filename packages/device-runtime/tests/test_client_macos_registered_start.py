"""Isolated callback sequencing; no service, filesystem or spawn actions."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Foundation compilation")


@pytest.mark.parametrize(
    "case", ["clear", "start-held", "runtime-held", "expired", "before", "after", "unknown"]
)
def test_registered_start_callback_once(tmp_path, case):
    source = tmp_path / "callback.m"
    header = Path(__file__).parents[1] / "native/client-macos-registered-start.h"
    source.write_text(
        "#import <Foundation/Foundation.h>\n#include <math.h>\n"
        "struct managed_guards {int start,runtime,management;};\n"
        "static int calls=0,checks=0;static double start_child_clock(void) {return 1;}\n"
        "static int registered_management_current(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g) {"
        "(void)m;(void)u;(void)p;(void)g;checks++;"
        f"return {'0' if case == 'before' else 'checks<2' if case == 'after' else '1'};}}\n"
        "static int request_bound_registered_start(const char *m,uid_t u,int fd,const char *t,double timeout) {"
        "(void)m;(void)u;if(fd!=7 || !t || !*t || timeout<=0 || timeout>10)return 0;calls++;"
        f"return {0 if case == 'unknown' else 1};}}\n"
        f'#include "{header}"\n'
        "int main(void) {@autoreleasepool {struct managed_guards g={"
        f"{8 if case == 'start-held' else -1},{9 if case == 'runtime-held' else -1},7}};"
        'struct registered_start_context c={"unused",0,@{},&g,"/tree",'
        f"{0 if case == 'expired' else 30},0}};"
        "int result=request_registered_start_once(&c);"
        f"if(result!={1 if case == 'clear' else 0})return 1;"
        "if(request_registered_start_once(&c)!=0 || !c.attempted)return 2;"
        f"if(calls!={1 if case in ('clear', 'after', 'unknown') else 0})return 3;"
        "if(g.management!=7)return 4;return 0;}}\n"
    )
    binary = tmp_path / "callback"
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
