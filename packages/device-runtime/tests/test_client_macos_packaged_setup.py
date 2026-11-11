"""Explicit offline package CLI checks; never register a real account/service."""

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("AI_DEVICE_TEST_MAC_TREE"), reason="explicit packaged tree required"
)


def verified_packaged_python(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("requires an unprivileged test process before any CLI execution")
    tree = Path(os.environ["AI_DEVICE_TEST_MAC_TREE"]).resolve()
    python = tree / "runtime/bin/python3"
    expected = json.loads((tree / "client-identity.json").read_text())
    identified = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import json; from device_runtime.version import runtime_version; print(json.dumps(runtime_version()))"],
        text=True, capture_output=True, cwd=tmp_path, timeout=10, check=True,
    )
    assert json.loads(identified.stdout) == expected
    assert expected["version"] == "0.3.4"
    assert expected["build_id"] == "sha256:0caf276fc87a739d3ec89f67090c91e9f70b716ec7a6153e1bd88665ff4d08ac"
    return python


def test_packaged_setup_cli_create_only_and_unprivileged_registration(tmp_path):
    python = verified_packaged_python(tmp_path)
    directory = tmp_path / "config"
    state = tmp_path / "state"
    token = "test-only-packaged-setup-token"
    command = [
        str(python), "-I", "-B", "-m", "device_runtime.client_setup",
        "--config-dir", str(directory), "--state-dir", str(state),
        "--server-url", "wss://example.test", "--device-id", "packaged-first",
        "--token-stdin",
    ]
    result = subprocess.run(command, input=token + "\n", text=True, capture_output=True,
                            cwd=tmp_path, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert token not in result.stdout + result.stderr
    assert "registration/start is still required" in result.stdout
    config = directory / "client.json"
    secret = directory / "token"
    before = config.read_bytes(), secret.read_bytes()
    assert json.loads(before[0])["state_dir"] == str(state)
    assert before[1] == (token + "\n").encode()
    assert config.stat().st_mode & 0o777 == 0o600
    assert secret.stat().st_mode & 0o777 == 0o600
    assert not state.exists()
    repeated = subprocess.run(command, input="different-test-only-token\n", text=True,
                              capture_output=True, cwd=tmp_path, timeout=10, check=False)
    assert repeated.returncode == 2
    assert "not overwritten" in repeated.stderr
    assert (config.read_bytes(), secret.read_bytes()) == before
    registration = subprocess.run(
        [str(python), "-I", "-B", "-m", "device_runtime.client_macos_register",
         "register", "--user", "nonexistent-offline-test-account",
         "--confirm-legacy-clients-disabled"],
        text=True, capture_output=True, cwd=tmp_path, timeout=10, check=False,
    )
    assert registration.returncode == 2
    assert not registration.stdout
    assert "refused or incomplete" in registration.stderr
    assert token not in registration.stderr
    assert (config.read_bytes(), secret.read_bytes()) == before


REGISTRATION_ADAPTER = r'''
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
from device_runtime import client_macos_register as reg
from device_runtime.client_config import load
from device_runtime.client_macos_layout import MacClientLayout
from device_runtime.client_macos_management import management_lock
from device_runtime.client_macos_identity import reserve_identity, read_identity
from device_runtime.version import runtime_version
base = Path(sys.argv[1]).resolve()
assert base.is_dir() and not base.is_symlink()
assert runtime_version()['build_id'] == 'sha256:0caf276fc87a739d3ec89f67090c91e9f70b716ec7a6153e1bd88665ff4d08ac'
assert os.geteuid() != 0
uid, gid = os.getuid(), os.getgid()
layout = MacClientLayout(uid, base / 'home')
management = base / 'management'
account = SimpleNamespace(name='test', layout=layout, gid=gid)
reg.os = SimpleNamespace(geteuid=lambda: 0)
reg.sys = SimpleNamespace(platform='darwin')
reg.resolve_account = lambda user: account if user == 'test' else None
reg.management_lock = lambda: management_lock(management, owner_uid=uid, owner_gid=gid)
reg.check_registration_conflicts = lambda **kwargs: None
reg.validate_launcher = lambda: None
reg.validate_registration_files = lambda account: None
def check_config(account):
    args, token = load(account.layout.config)
    assert args.state_dir == layout.state and len(token) >= 16
reg.validate_config_as_user = check_config
reg.reserve_identity = lambda account: reserve_identity(account, directory=management)
reg.read_identity = lambda: read_identity(directory=management)
if sys.argv[2] == 'fail':
    def fail_publish(layout):
        raise OSError('offline publication fault')
    reg.publish_agent = fail_publish
reg.main([sys.argv[3], '--user', 'test', '--confirm-legacy-clients-disabled'])
'''


@pytest.mark.parametrize("fail_publish", [False, True])
def test_packaged_registration_cli_with_isolated_account_adapter(tmp_path, fail_publish):
    python = publish_first_packaged_tree(tmp_path, verified_packaged_python(tmp_path))
    directory = tmp_path / "home/Library/Application Support/ai-device-client"
    directory.parent.mkdir(parents=True, mode=0o700)
    state = directory / "state"
    setup = subprocess.run(
        [str(python), "-I", "-B", "-m", "device_runtime.client_setup",
         "--config-dir", str(directory), "--state-dir", str(state),
         "--server-url", "wss://example.test", "--device-id", "packaged-register", "--token-stdin"],
        input="test-only-register-process-token\n", text=True, capture_output=True,
        cwd=tmp_path, timeout=10, check=True,
    )
    assert "registration/start is still required" in setup.stdout
    before = [(directory / name).read_bytes() for name in ("client.json", "token")]

    def invoke(mode, action):
        return subprocess.run(
            [str(python), "-I", "-B", "-c", REGISTRATION_ADAPTER, str(tmp_path), mode, action],
            text=True, capture_output=True, cwd=tmp_path, timeout=10, check=False,
        )

    first = invoke("fail" if fail_publish else "normal", "register")
    record = tmp_path / "management/registration.json"
    plist = tmp_path / "home/Library/LaunchAgents/local.ai-device.client.plist"
    if fail_publish:
        assert first.returncode == 2, first.stderr
        assert not first.stdout and "refused or incomplete" in first.stderr
        assert not plist.exists()
        identity_before = record.read_bytes()
        inodes = [(directory / name).stat().st_ino for name in ("runtime.lock", "start.lock")]
        first = invoke("normal", "resume")
        assert record.read_bytes() == identity_before
        assert [(directory / name).stat().st_ino for name in ("runtime.lock", "start.lock")] == inodes
    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result == {"registered": True, "started": False, "uid": os.getuid(), "plist": str(plist)}
    import plistlib

    from device_runtime.client_macos_layout import MacClientLayout

    assert plistlib.loads(plist.read_bytes()) == MacClientLayout(os.getuid(), tmp_path / "home").definition()
    assert [(directory / name).read_bytes() for name in ("client.json", "token")] == before
    assert "test-only-register-process-token" not in first.stdout + first.stderr


def publish_first_packaged_tree(tmp_path, python):
    """Real create-only publisher; temporary paths and host conflict adapter only."""
    import importlib.util
    import shutil

    package = Path(__file__).parents[1]
    native = package / "native"
    spec = importlib.util.spec_from_file_location(
        "first_registration_builder", package / "scripts/build-client-nopayload-inspection.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = tmp_path / "resources/client-tree"
    shutil.copytree(python.parents[2], source, symlinks=True)
    build = tmp_path / "compile"
    manifest = builder.INVENTORY.inventory(source)
    builder.compile_resource_check(manifest, tmp_path, build)
    management = tmp_path / "management"
    management.mkdir(mode=0o700)
    (management / "management.lock").touch(mode=0o600)
    target = tmp_path / "published-program"
    harness = tmp_path / "publish-first.m"
    harness.write_text(
        '#define main offline_writer_main\n#define EMBEDDED_RESOURCE_CHECK\n'
        f'#include "{native / "client-macos-writer-probe.c"}"\n'
        '#undef main\n#define IDENTITY_LIBRARY\n'
        f'#include "{native / "client-macos-identity.m"}"\n'
        f'#include "{native / "client-macos-bound-resources.h"}"\n'
        f'#include "{native / "client-macos-first-publish.h"}"\n'
        'static int clear(void *p){(void)p;return 1;}\n'
        'int main(int argc,char **argv){@autoreleasepool{\n'
        ' (void)managedPaths;(void)observe_unregistered_program;\n'
        ' if(argc!=3)return 64;int fd=acquireManagement(argv[1],getuid());if(fd<0)return 70;\n'
        ' int r=publish_first_program(argv[1],getuid(),fd,argv[2],clear,NULL);\n'
        ' if(acquireManagement(argv[1],getuid())!=-2)return 71;\n'
        ' close(fd);return r;}}\n'
    )
    binary = tmp_path / "publish-first"
    subprocess.run(
        ["/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-fobjc-arc",
         "-arch", "arm64", "-framework", "Foundation", "-I", str(build), str(harness), "-o", str(binary)],
        capture_output=True, timeout=30, check=True,
    )
    command = [str(binary), str(management), str(target)]
    subprocess.run(command, capture_output=True, timeout=15, check=True)
    builder.INVENTORY.verify(target, manifest)
    before = target.stat().st_ino
    repeated = subprocess.run(command, capture_output=True, timeout=15, check=False)
    assert repeated.returncode != 0
    assert target.stat().st_ino == before
    builder.INVENTORY.verify(target, manifest)
    assert not list(tmp_path.glob(".ai-device-client-stage-*"))
    assert not (management / "registration.json").exists()
    return target / "runtime/bin/python3"
