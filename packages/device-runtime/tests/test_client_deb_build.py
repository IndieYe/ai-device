import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/build-client-deb.py"
spec = importlib.util.spec_from_file_location("client_deb_build", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_build_hooks_and_layout(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    binary = tree / "runtime/bin/python3"
    binary.parent.mkdir(parents=True)
    binary.write_text("fixture")
    cache = tree / "runtime/lib/__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.cpython-313.pyc").write_bytes(b"cache")
    (cache.parent / "legacy.pyc").write_bytes(b"cache")
    (cache.parent / "module.py").write_text("pass")
    original = subprocess.run
    calls = []

    def run(argv, **kwargs):
        if argv[0] == "sh":
            return original(argv, **kwargs)
        calls.append(argv)

    monkeypatch.setattr(module.subprocess, "run", run)
    output = tmp_path / "build"
    result = module.build(tree, output, "0.3.4~dev3")
    assert result.name == "ai-device-client_0.3.4~dev3_amd64.deb"
    assert (output / "root/opt/ai-device-client/runtime/bin/python3").is_file()
    payload = output / "root/opt/ai-device-client"
    assert not list(payload.rglob("*.pyc"))
    assert not list(payload.rglob("__pycache__"))
    assert (payload / "runtime/lib/module.py").is_file()
    entry = output / "root/usr/bin/ai-device-client"
    assert 'device_runtime.client_cli "$@"' in entry.read_text()
    assert entry.stat().st_mode & 0o777 == 0o755
    assert calls[0][:2] == ["dpkg-deb", "--root-owner-group"]
    prerm = (output / "root/DEBIAN/prerm").read_text()
    assert "upgrade|remove|deconfigure" in prerm
    assert "inactive:0:0|failed:0:0" in prerm
    assert "python3 -I -B -m device_runtime.client_package stop" in prerm
    postrm = (output / "root/DEBIAN/postrm").read_text()
    assert postrm.index("grep -Fxq") < postrm.index("systemctl disable")
    assert "/var/lib/ai-device-client" not in postrm
    removals = [line.strip() for line in postrm.splitlines() if line.strip().startswith("rm ")]
    assert removals == [
        "rm /etc/systemd/system/ai-device-client.service",
        "rm -rf /etc/ai-device-client || exit 1",
    ]
    # State directories hold the device journal and must never be swept here.
    assert "state" not in postrm.replace("state-dir", "")
    # purge removes configuration and credentials; a plain remove keeps them.
    purge = postrm.split("purge)", 2)[-1]
    assert "/etc/ai-device-client" in purge
    remove_only = postrm.split("purge)")[0]
    assert "rm -rf /etc/ai-device-client" not in remove_only
    # Interpreter caches are created at run time and are not in the manifest.
    assert "__pycache__" in postrm
    assert "-prune" in postrm
    # Caches are often the only content of their directory, so dpkg's own removal
    # leaves the parents behind; prune only what is empty, never a directory
    # that still holds files.
    assert "-depth -type d -empty -delete" in postrm


@pytest.mark.parametrize("version", ["", "bad", "1\nInjected: yes", "1;false"])
def test_invalid_version_before_writes(tmp_path, version):
    with pytest.raises(ValueError):
        module.build(tmp_path, tmp_path / "output", version)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("state", ["active", "inactive"])
def test_overrides_rejected_before_python_or_state_probe(tmp_path, state):
    # Execute the actual shell fragment with an isolated systemctl, rather than
    # merely checking that a guard string exists in the generated package.
    import os

    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        '#!/bin/sh\ncase "$*" in\n'
        "*FragmentPath*) echo /etc/systemd/system/ai-device-client.service ;;\n"
        "*DropInPaths*) echo /etc/systemd/system/ai-device-client.service.d/test.conf ;;\n"
        f"*ActiveState*) echo {state}; exit 77 ;;\n"
        "*) exit 78 ;;\nesac\n"
    )
    systemctl.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", "-c", module.STOP],
        env={**os.environ, "PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "unsupported overrides" in result.stderr


def test_same_interpreter_unrelated_service_refused_before_systemctl(tmp_path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "ai-device-client.service").write_text(
        "[Service]\nExecStart=/opt/ai-device-client/runtime/bin/python3 -m unrelated\n"
    )
    script = module.STOP.replace("/etc/systemd/system", str(unit_dir))
    result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "unrelated client service" in result.stderr


def test_postrm_rejects_unrelated_command_without_disabling(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    binary = tree / "runtime/bin/python3"
    binary.parent.mkdir(parents=True)
    binary.write_text("fixture")
    original_run = subprocess.run
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: None)
    output = tmp_path / "output"
    module.build(tree, output, "0.3.4~test")
    units = tmp_path / "units"
    units.mkdir()
    unit = units / "ai-device-client.service"
    content = "ExecStart=/opt/ai-device-client/runtime/bin/python3 -m unrelated\n"
    unit.write_text(content)
    script = (output / "root/DEBIAN/postrm").read_text().replace(
        "/etc/systemd/system", str(units)
    )
    # Replace systemctl with a visible marker; no real service operation.
    marker = tmp_path / "systemctl-called"
    script = script.replace("systemctl ", f"touch {marker}; false ")
    result = original_run(["/bin/sh", "-c", script, "postrm", "remove"], check=False)
    assert result.returncode != 0
    assert unit.read_text() == content
    assert not marker.exists()


def test_postrm_prunes_only_empty_directories(tmp_path, monkeypatch):
    """A cache-only stdlib directory must not survive; a populated one must."""
    tree = tmp_path / "tree"
    binary = tree / "runtime/bin/python3"
    binary.parent.mkdir(parents=True)
    binary.write_text("fixture")
    original_run = subprocess.run
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: None)
    output = tmp_path / "output"
    module.build(tree, output, "0.3.4~test")

    program = tmp_path / "opt/ai-device-client"
    cache_only = program / "runtime/lib/python3.13/json"
    cache_only.mkdir(parents=True)
    (cache_only / "__pycache__").mkdir()
    (cache_only / "__pycache__/codec.pyc").write_text("cache")
    kept = program / "runtime/lib/python3.13/asyncio"
    kept.mkdir(parents=True)
    (kept / "tasks.py").write_text("source")

    units = tmp_path / "units"
    units.mkdir()
    script = (output / "root/DEBIAN/postrm").read_text()
    script = script.replace("/etc/systemd/system", str(units))
    script = script.replace("/opt/ai-device-client", str(program))
    script = script.replace("systemctl ", "true # ")

    result = original_run(
        ["/bin/sh", "-c", script, "postrm", "remove"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert not cache_only.exists(), "cache-only directory must be pruned"
    assert (kept / "tasks.py").is_file(), "contents must never be removed"
