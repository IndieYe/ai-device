"""Fixed observation entry with recording substitutes; no system paths accessed."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin compiler")


@pytest.mark.parametrize(
    "case",
    [
        "ready",
        "target",
        "unknown",
        "lock",
        "identity",
        "paths",
        "user",
        "euser",
        "missing",
        "extra",
        "other",
        "clock",
    ],
)
def test_recovery_entry_contract(tmp_path, case):
    native = Path(__file__).parents[1] / "native"
    source = tmp_path / "entry.m"
    permitted = case not in ("user", "euser", "missing", "extra", "other", "clock")
    observed = case in ("ready", "target", "unknown")
    source.write_text(
        "#import <Foundation/Foundation.h>\n#include <unistd.h>\n#include <stdio.h>\n#include <math.h>\n#include <string.h>\n"
        "struct managed_guards {int management,start,runtime; NSDictionary *__strong identity;};\n"
        "enum registered_recovery_observation {RECOVERY_UNKNOWN,RECOVERY_TARGET_EXACT,RECOVERY_READY_EXACT};\n"
        "static int acquired=0,read_count=0,observed=0,released=0;\n"
        f"static uid_t test_uid(void){{return {501 if case == 'user' else 0};}}\n"
        f"static uid_t test_euid(void){{return {501 if case == 'euser' else 0};}}\n"
        "#define getuid test_uid\n#define geteuid test_euid\n"
        f"static double lease_clock(void){{return {'NAN' if case == 'clock' else '1'};}}\n"
        'static int acquireManagement(const char *p,uid_t u){acquired++;if(u || strcmp(p,"/Library/Application Support/ai-device-client-management"))abort();'
        f"return {-1 if case == 'lock' else 7};}}\n"
        "static NSDictionary *readIdentity(const char *p,uid_t u){(void)p;(void)u;read_count++;"
        f"return {'nil' if case == 'identity' else '@{}'};}}\n"
        "static NSDictionary *managedPaths(NSDictionary *i){(void)i;"
        f"return {'nil' if case == 'paths' else '@{}'};}}\n"
        "static enum registered_recovery_observation observe_registered_recovery(const char *m,uid_t u,NSDictionary *p,struct managed_guards *g,double d){"
        "(void)m;(void)u;(void)p;observed++;if(g->management!=7 || g->start!=-1 || g->runtime!=-1 || !g->identity || d!=11)abort();"
        f"return {2 if case == 'ready' else 1 if case == 'target' else 0};}}\n"
        "static void release_guards(struct managed_guards *g){released++;if(g->start!=-1 || g->runtime!=-1)abort();g->management=-1;g->identity=nil;}\n"
        f'#include "{native / "client-macos-recovery-entry.h"}"\n'
        "int main(int argc,char **argv){@autoreleasepool {int r=recovery_observe_entry(argc,argv);"
        f"if(acquired!={int(permitted)} || released!={int(permitted)} || observed!={int(observed)} || read_count!={int(permitted and case != 'lock')})return 99;return r;}}}}\n"
    )
    binary = tmp_path / "entry"
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
    args = (
        []
        if case == "missing"
        else ["other"]
        if case == "other"
        else ["recovery-observe", "extra"]
        if case == "extra"
        else ["recovery-observe"]
    )
    result = subprocess.run(
        [str(binary), *args], check=False, capture_output=True, text=True, timeout=5
    )
    assert result.returncode == (
        64 if case in ("missing", "extra", "other") else 0 if case in ("ready", "target") else 70
    )
    token = "READY_EXACT" if case == "ready" else "TARGET_EXACT" if case == "target" else "UNKNOWN"
    assert result.stdout == (f"AI_DEVICE_RECOVERY_{token}_V1\n" if permitted else "")


def test_production_observer_compiles_without_execution(tmp_path):
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location(
        "recovery_entry_builder", root / "scripts/build-client-nopayload-inspection.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = tmp_path / "source"
    source.mkdir()
    (source / "version").write_text("compile-only")
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), tmp_path, build)
    result = subprocess.run(
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
            str(root / "native/client-macos-recovery-observer.m"),
            "-o",
            str(tmp_path / "observer-not-executed"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
