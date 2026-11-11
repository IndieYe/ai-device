"""Real inventory-verified metadata files, no service or installed tree."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Foundation")


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "missing",
        "invalid-json",
        "bad-version",
        "bad-build",
        "boolean",
        "extra",
        "oversize",
        "changed",
    ],
)
def test_target_identity(tmp_path, case):
    source = tmp_path / "tree"
    source.mkdir()
    identity = {"version": "0.3.4", "build_id": "sha256:" + "a" * 64}
    if case == "bad-version":
        identity["version"] = "bad version"
    elif case == "bad-build":
        identity["build_id"] = "sha256:" + "A" * 64
    elif case == "boolean":
        identity["version"] = True
    elif case == "extra":
        identity["extra"] = "no"
    path = source / "client-identity.json"
    if case != "missing":
        path.write_text(
            "not json"
            if case == "invalid-json"
            else "x" * 4097
            if case == "oversize"
            else json.dumps(identity)
        )
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("identity_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), tmp_path, build)
    if case == "changed":
        path.write_text(json.dumps({**identity, "version": "other"}))
    native = Path(__file__).parents[1] / "native"
    harness = tmp_path / "identity.m"
    harness.write_text(
        "#import <Foundation/Foundation.h>\n"
        "#define main offline_writer_main\n#define EMBEDDED_RESOURCE_CHECK\n"
        f'#include "{native / "client-macos-writer-probe.c"}"\n#undef main\n'
        f'#include "{native / "client-macos-target-identity.h"}"\n'
        "int main(int argc,char **argv){(void)argc;@autoreleasepool {\n"
        "NSDictionary *v=read_target_identity(argv[1],getuid());\n"
        'if(v && (![v[@"version"] isEqual:@"0.3.4"] || [v[@"build_id"] length]!=71))return 3;\n'
        'printf("%d\\n",v!=nil);return 0;}}\n'
    )
    binary = tmp_path / "identity"
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
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [str(binary), str(source)], check=True, capture_output=True, text=True, timeout=3
    )
    assert result.stdout == ("1\n" if case == "valid" else "0\n")
