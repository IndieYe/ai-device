"""Rebuild sdists and test installed wheels outside the checkout (no real devices)."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd, env):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default="3.13")
    parser.add_argument("--fs-python", default=None)
    args = parser.parse_args()
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(
            ("DEVICE_", "MCP_", "PYTHONPATH", "UV_PROJECT", "VIRTUAL_ENV")
        ):
            env.pop(key, None)
    env["UV_NO_CACHE"] = "1"
    with tempfile.TemporaryDirectory(prefix="ai-device-artifacts-") as directory:
        work = Path(directory)
        wheels = work / "wheels"
        wheels.mkdir()
        for archive in sorted((ROOT / "dist").glob("*.tar.gz")):
            unpack = work / archive.name.removesuffix(".tar.gz")
            unpack.mkdir()
            with tarfile.open(archive) as tar:
                tar.extractall(unpack, filter="data")
            source = next(unpack.iterdir())
            run(
                ["uv", "build", "--wheel", "--out-dir", str(wheels), str(source)],
                work,
                env,
            )
        artifacts = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in wheels.glob("*.whl")
        }
        assert len(artifacts) == 3, artifacts
        names = {
            "bash": "ai_device_bash",
            "fs": "ai_device_fs",
            "device-runtime": "ai_device",
        }
        for package in ("bash", "fs"):
            wheel = next(wheels.glob(names[package] + "-*.whl"))
            for extras in ("", "[mcp]"):
                python = (
                    args.fs_python
                    if package == "fs" and args.fs_python
                    else args.python
                )
                probe = f"""
import importlib, importlib.util, pathlib, sys
module = importlib.import_module('mcp_server_{package}.core')
assert pathlib.Path(module.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()), module.__file__
"""
                if not extras:
                    probe += "assert importlib.util.find_spec('fastmcp') is None\n"
                else:
                    probe += f"importlib.import_module('mcp_server_{package}.server')\n"
                isolated = work / (package + ("-mcp" if extras else "-base"))
                run(["uv", "venv", "--python", python, str(isolated)], work, env)
                executable = isolated / "bin/python"
                run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        str(executable),
                        str(wheel) + extras,
                    ],
                    work,
                    env,
                )
                run([str(executable), "-I", "-c", probe], work, env)
                if package == "fs":
                    run(
                        ["uv", "pip", "install", "--python", str(executable), "pytest"],
                        work,
                        env,
                    )
                    suite = work / ("fs-mcp-tests" if extras else "fs-base-tests")
                    suite.mkdir()
                    files = [
                        "core/test_standalone_core.py",
                        "core/test_read_ranges.py",
                    ]
                    if extras:
                        files += [
                            "patch/test_apply_patch.py",
                            "core/test_local_only_contract.py",
                            "core/test_server_core.py",
                            "core/test_read_image.py",
                            "core/test_tool_behaviors.py",
                            "core/test_edit_file_schema.py",
                        ]
                    for relative in files:
                        shutil.copy2(
                            ROOT / "packages/fs/tests" / relative,
                            suite / Path(relative).name,
                        )
                    config = suite / "pytest.ini"
                    config.write_text("[pytest]\n")
                    expression = "not built_wheel and not inspector"
                    if not extras:
                        expression += " and not mcp_diff"
                    run(
                        [
                            str(executable),
                            "-I",
                            "-m",
                            "pytest",
                            "-c",
                            str(config),
                            str(suite),
                            "-q",
                            "-k",
                            expression,
                        ],
                        work,
                        env,
                    )
        venv = work / "venv"
        run(["uv", "venv", "--python", args.python, str(venv)], work, env)
        python = venv / "bin/python"
        requirements = [
            str(next(wheels.glob("ai_device-*.whl"))) + "[client,server,ops,mcp]",
            str(next(wheels.glob("ai_device_bash-*.whl"))),
            str(next(wheels.glob("ai_device_fs-*.whl"))),
            "pytest",
            "pytest-asyncio",
        ]
        run(["uv", "pip", "install", "--python", str(python), *requirements], work, env)
        run(
            [
                str(python),
                "-I",
                "-c",
                "import pathlib,sys,device_runtime,mcp_server_bash,mcp_server_fs; assert all(pathlib.Path(m.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()) for m in (device_runtime,mcp_server_bash,mcp_server_fs))",
            ],
            work,
            env,
        )
        shutil.copytree(
            ROOT / "packages/device-runtime/scripts",
            work / "scripts",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        tests = work / "tests"
        shutil.copytree(
            ROOT / "packages/device-runtime/tests",
            tests,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (work / "pytest.ini").write_text(
            "[pytest]\nasyncio_mode = auto\nasyncio_default_fixture_loop_scope = function\n"
        )
        run(
            [
                str(python),
                "-I",
                "-m",
                "pytest",
                "-c",
                str(work / "pytest.ini"),
                str(tests),
                "-q",
            ],
            work,
            env,
        )
        report = {
            "python": args.python,
            "fs_python": args.fs_python,
            "rebuilt_wheels": artifacts,
            "scope": "sdist rebuild, isolated base/MCP imports, installed-wheel Device full tests; no real deployment",
        }
        (ROOT / "artifacts").mkdir(exist_ok=True)
        (ROOT / "artifacts/artifact-verification.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )


if __name__ == "__main__":
    main()
