"""Registered-upgrade entry contract. Substitutes only the leaf coordinator."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin compiler")


@pytest.mark.parametrize(
    "case,args,expected",
    [
        ("missing-confirm", ["upgrade-registered-program"], 64),
        ("wrong-confirm", ["upgrade-registered-program", "--force"], 64),
        ("extra-arg", ["upgrade-registered-program", "--confirm-idle-instance-exits", "x"], 64),
        ("ordinary-user", ["upgrade-registered-program", "--confirm-idle-instance-exits"], 70),
    ],
)
def test_upgrade_entry_refuses_without_effect(tmp_path, case, args, expected):
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "upgrade-entry.m"
    harness.write_text(
        "#import <Foundation/Foundation.h>\n#include <stdio.h>\n#include <string.h>\n#include <unistd.h>\n"
        "#include <math.h>\n"
        'static uid_t test_uid(void) {return 501;}\n'
        'static uid_t test_euid(void) {return 501;}\n'
        "#define getuid test_uid\n#define geteuid test_euid\n"
        f'#include "{native}/client-macos-upgrade-stages.h"\n'
        "struct managed_guards {int management,start,runtime;NSDictionary *__strong identity;};\n"
        "static void release_guards(struct managed_guards *g){(void)g;}\n"
        "static NSDictionary *prepare_start_barrier(const char *m,uid_t u,"
        "struct managed_guards *g){(void)m;(void)u;(void)g;return @{};}\n"
        "static double lease_clock(void){return -1;}\n"
        "static int coordinator_calls=0;\n"
        "static int coordinator_stage=UPGRADE_STAGE_OK;\n"
        "static int recovery_calls=0;\n"
        "static int upgrade_registered_bound_once(const char *m,uid_t u,NSDictionary *p,"
        "struct managed_guards *g,double d,int (*s)(void *),void *c){"
        "(void)m;(void)u;(void)p;(void)g;(void)d;(void)s;(void)c;coordinator_calls++;"
        "return coordinator_stage;}\n"
        "static int recovery_rc=0;\n"
        "static int upgrade_recover_after_stage(const char *m,uid_t u,NSDictionary *p,"
        "struct managed_guards *g,int stage,double d){"
        "(void)m;(void)u;(void)p;(void)g;(void)stage;(void)d;"
        "recovery_calls++;return recovery_rc;}\n"
        f'#include "{native}/client-macos-upgrade-entry.h"\n'
        "int main(int argc,char **argv){int r=upgrade_registered_entry(argc,argv);"
        "return coordinator_calls==0?r:96;}\n"
    )
    binary = tmp_path / "upgrade-entry"
    compile = subprocess.run(
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
        check=False,
        capture_output=True,
        text=True,
    )
    assert compile.returncode == 0, compile.stderr
    result = subprocess.run([str(binary), *args], check=False, capture_output=True, text=True, timeout=3)
    assert result.returncode == expected
    assert result.stdout == ""


def test_upgrade_entry_reports_unknown_without_claiming_ready(tmp_path):
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "upgrade-entry-unknown.m"
    harness.write_text(
        "#import <Foundation/Foundation.h>\n#include <stdio.h>\n#include <string.h>\n#include <unistd.h>\n"
        "#include <math.h>\n"
        'static uid_t test_uid(void) {return 0;}\n'
        'static uid_t test_euid(void) {return 0;}\n'
        "#define getuid test_uid\n#define geteuid test_euid\n"
        f'#include "{native}/client-macos-upgrade-stages.h"\n'
        "struct managed_guards {int management,start,runtime;NSDictionary *__strong identity;};\n"
        "static int released=0;\n"
        "static void release_guards(struct managed_guards *g){(void)g;released++;}\n"
        "static NSDictionary *prepare_start_barrier(const char *m,uid_t u,"
        "struct managed_guards *g){(void)m;(void)u;(void)g;return @{};}\n"
        "static double lease_clock(void){return 1;}\n"
        "static int recovery_calls=0;\n"
        "static int upgrade_registered_bound_once(const char *m,uid_t u,NSDictionary *p,"
        "struct managed_guards *g,double d,int (*s)(void *),void *c){"
        "(void)m;(void)u;(void)p;(void)g;(void)d;(void)s;(void)c;"
        "return UPGRADE_STAGE_REFUSED;}\n"
        "static int upgrade_recover_after_stage(const char *m,uid_t u,NSDictionary *p,"
        "struct managed_guards *g,int stage,double d){"
        "(void)m;(void)u;(void)p;(void)g;(void)stage;(void)d;"
        "recovery_calls++;return 1;}\n"
        f'#include "{native}/client-macos-upgrade-entry.h"\n'
        "int main(int argc,char **argv){int r=upgrade_registered_entry(argc,argv);"
        "return (released==1 && recovery_calls==0)?r:97;}\n"
    )
    binary = tmp_path / "upgrade-entry-unknown"
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
        [str(binary), "upgrade-registered-program", "--confirm-idle-instance-exits"],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.returncode == 70
    # Stage-specific token: REFUSED means nothing irreversible happened, and no
    # recovery is attempted for that stage.
    assert result.stdout == "AI_DEVICE_UPGRADE_REFUSED_V1\n"
