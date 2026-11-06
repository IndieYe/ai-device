"""Recording entry contract, no production root paths or host scans."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin compiler")


@pytest.mark.parametrize(
    "case", ["clear", "failed", "lock", "user", "euser", "missing", "confirmation", "extra"]
)
def test_unregistered_observation_entry(tmp_path, case):
    header = Path(__file__).parents[1] / "native/client-macos-unregistered-entry.h"
    source = tmp_path / "entry.c"
    allowed = case not in ("user", "euser", "missing", "confirmation", "extra")
    called = allowed and case != "lock"
    source.write_text(
        "#include <stdio.h>\n#include <unistd.h>\n#include <string.h>\n#include <stdlib.h>\n"
        "static int acquired=0,observed=0,closed=0;\n"
        f"static uid_t test_uid(void){{return {501 if case == 'user' else 0};}}\n"
        f"static uid_t test_euid(void){{return {501 if case == 'euser' else 0};}}\n"
        "#define getuid test_uid\n#define geteuid test_euid\n"
        'static int acquireManagement(const char *p,uid_t u){acquired++;if(u || strcmp(p,"/Library/Application Support/ai-device-client-management"))abort();'
        f"return {-1 if case == 'lock' else 7};}}\n"
        "static int observe_unregistered_program_with_host_check(const char *m,uid_t u,int f,const char *t,int c,double d){observed++;"
        'if(strcmp(m,"/Library/Application Support/ai-device-client-management") || u || f!=7 || strcmp(t,"/opt/ai-device-client") || c!=1 || d!=10)abort();'
        f"return {70 if case == 'failed' else 0};}}\n"
        "static int test_close(int f){if(f!=7)abort();closed++;return 0;}\n#define close test_close\n"
        f'#include "{header}"\n'
        "int main(int argc,char **argv){int r=unregistered_observe_entry(argc,argv);"
        f"if(acquired!={int(allowed)} || observed!={int(called)} || closed!={int(called)})return 99;return r;}}\n"
    )
    binary = tmp_path / "entry"
    subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(binary)],
        check=True,
        capture_output=True,
    )
    args = ["observe-unregistered-program", "--confirm-legacy-clients-disabled"]
    if case == "missing":
        args = args[:1]
    if case == "confirmation":
        args[1] = "wrong"
    if case == "extra":
        args.append("extra")
    result = subprocess.run(
        [str(binary), *args], check=False, capture_output=True, text=True, timeout=5
    )
    assert result.returncode == (
        64 if case in ("missing", "confirmation", "extra") else 0 if case == "clear" else 70
    )
    assert result.stdout == (
        "AI_DEVICE_UNREGISTERED_PROGRAM_EXACT_NOT_REGISTERED_V1\n" if case == "clear" else ""
    )
