"""Connection deadline gate; recording socket seam, never real services."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Foundation compiler")


@pytest.mark.parametrize("clock", ["-1", "NAN", "INFINITY", "10", "0"])
def test_connection_clock_gate_before_socket(tmp_path, clock):
    header = Path(__file__).parents[1] / "native/client-macos-registered-connect.h"
    source = tmp_path / "connect.m"
    source.write_text(
        "#import <Foundation/Foundation.h>\n#include <sys/socket.h>\n#include <sys/un.h>\n#include <unistd.h>\n#include <poll.h>\n#include <errno.h>\n#include <math.h>\n"
        "static int sockets=0;\n"
        f"static double lease_clock(void){{return {clock};}}\n"
        "static int lease_wait(int f,short e,double d){(void)f;(void)e;(void)d;return 0;}\n"
        "static int test_socket(int d,int t,int p){(void)d;(void)t;(void)p;sockets++;return -1;}\n"
        "#define socket test_socket\n"
        f'#include "{header}"\n'
        'int main(void){@autoreleasepool {int r=connect_registered_socket(@{@"directory":@"/unused"},10);'
        f"return r==-1 && sockets=={int(clock == '0')} ? 0 : 1;}}}}\n"
    )
    binary = tmp_path / "connect"
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
