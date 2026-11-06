"""Fixed entry contract with linked recording substitute, never system writes."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Darwin compiler")


@pytest.mark.parametrize(
    "case", ["clear", "failed", "real-user", "effective-user", "missing", "extra", "other-action"]
)
def test_fixed_first_entry(tmp_path, case):
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "entry.c"
    harness.write_text(
        "#include <stdio.h>\n#include <string.h>\n#include <unistd.h>\n"
        f"static uid_t test_uid(void) {{return {501 if case == 'real-user' else 0};}}\n"
        f"static uid_t test_euid(void) {{return {501 if case == 'effective-user' else 0};}}\n"
        "#define getuid test_uid\n#define geteuid test_euid\n"
        "static int called=0;\n"
        "static int prepare_and_publish_first_program(const char *directory,uid_t owner,const char *target,int confirm,double timeout,int *fd) {\n"
        'called++;if(strcmp(directory,"/Library/Application Support/ai-device-client-management") || owner ||\n'
        'strcmp(target,"/opt/ai-device-client") || confirm!=1 || timeout!=10 || *fd!=-1)return 99;\n'
        + f"return {70 if case == 'failed' else 0};}}\n"
        f'#include "{native / "client-macos-first-entry.h"}"\n'
        "int main(int argc,char **argv) {int result=first_install_entry(argc,argv);\n"
        + f"if(called!={1 if case in ('clear', 'failed') else 0})return 98;return result;}}\n"
    )
    binary = tmp_path / "entry"
    subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", str(harness), "-o", str(binary)],
        check=True,
        capture_output=True,
    )
    args = ["first-program", "--confirm-legacy-clients-disabled"]
    if case == "missing":
        args.pop()
    elif case == "extra":
        args += ["--source", "/other"]
    elif case == "other-action":
        args[0] = "upgrade"
    result = subprocess.run(
        [str(binary), *args], check=False, capture_output=True, text=True, timeout=3
    )
    assert result.returncode == (
        0 if case == "clear" else 64 if case in ("missing", "extra", "other-action") else 70
    )
    assert result.stdout == (
        "AI_DEVICE_FIRST_PROGRAM_PUBLISHED_NOT_REGISTERED_V1\n" if case == "clear" else ""
    )


def test_production_entry_compiles_without_execution(tmp_path):
    native = Path(__file__).parents[1] / "native"
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("first_entry_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = tmp_path / "source"
    source.mkdir()
    (source / "version").write_text("compile-only")
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), tmp_path, build)
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
            str(native / "client-macos-installer.m"),
            "-o",
            str(tmp_path / "installer-not-executed"),
        ],
        check=True,
        capture_output=True,
    )
