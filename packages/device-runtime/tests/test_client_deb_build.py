import importlib.util
import os
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


def test_stop_explains_the_failure_consequence():
    """dpkg cannot roll back a stop when unpacking fails, so say so up front."""
    assert "reinstall a complete package to repair" in module.STOP
    # The note must precede the stop, not follow it: after the stop the
    # remaining scripts see an inactive unit and would skip the message.
    note = module.STOP.index("reinstall a complete package to repair")
    stop = module.STOP.index("client_package stop")
    assert note < stop


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


def test_depends_declares_the_real_glibc_floor(tmp_path, monkeypatch):
    """The declared floor must match what the tree actually needs.

    Shipping the build host's glibc here silently rejects every older host that
    can run the package, so the declaration is checked explicitly.
    """
    tree = tmp_path / "tree"
    binary = tree / "runtime/bin/python3"
    binary.parent.mkdir(parents=True)
    binary.write_text("fixture")
    monkeypatch.setattr(module.subprocess, "run", lambda argv, **kwargs: None)

    module.build(tree, tmp_path / "build", "0.4.2")
    control = (tmp_path / "build/root/DEBIAN/control").read_text()

    assert f"libc6 (>= {module.MINIMUM_GLIBC})" in control
    # Guards against reintroducing the build host's version as the floor.
    assert "libc6 (>= 2.39)" not in control
    assert control.count("libc6 (>= ") == 1


def test_declared_glibc_floor_matches_the_runtime_requirement():
    """Builders declare a literal so they can run without importing the package.

    That duplication is only safe if something checks it, which is this test.
    """
    from device_runtime import dependencies
    from device_runtime.client_host_glibc import MINIMUM_GLIBC as CLIENT_FLOOR

    assert module.MINIMUM_GLIBC == dependencies.MINIMUM_GLIBC == CLIENT_FLOOR
    assert module.MINIMUM_GLIBC == dependencies.TARGET["glibc"]
    assert module.MINIMUM_GLIBC in dependencies.SUPPORTED_GLIBC


def _run_stop(tmp_path, cgroup_root, group="/system.slice/client.service", state="failed"):
    """Execute the generated cgroup guard against a synthetic hierarchy.

    Only the cgroup section runs: the rest needs systemd. The script fragment is
    extracted from STOP so the test exercises the shipped text, not a copy.
    """
    start = module.STOP.index("# Check descendants too")
    fragment = module.STOP[start:]
    harness = (
        "set -eu\n"
        "unit=ai-device-client.service\n"
        f"systemctl() {{ printf '%s\\n' '{group}'; }}\n"
        + fragment
    )
    script = tmp_path / "guard.sh"
    script.write_text(harness)
    env = {"PATH": "/usr/bin:/bin", "AI_DEVICE_CGROUP_ROOT": str(cgroup_root)}
    return subprocess.run(
        ["sh", str(script)], capture_output=True, text=True, env=env, check=False
    )


def _pseudo_file(path, content):
    """Create a file that stat reports as zero bytes, like the kernel's cgroup
    pseudo-files.

    `cgroup.procs` and `cgroup.events` are generated on read; their st_size is 0
    even when they list processes. A regular temporary file has a real size, so
    a test built on one cannot tell a content check from a size check. A FIFO
    reproduces the property that matters: stat size 0, content available on
    read. A writer opens it in the background so the guard's `cat` sees content
    instead of blocking.
    """
    os.mkfifo(path)
    # Hold the writer's stdin from the FIFO's open until the guard reads it.
    subprocess.Popen(["sh", "-c", "printf '%s' \"$0\" > \"$1\"", content, str(path)])
    return path


def test_stop_refuses_when_v1_group_has_processes(tmp_path):
    """Legacy hierarchy: a live process must block replacement.

    Under the old single-path check this directory was never found, so the
    guard silently passed on every v1 host.
    """
    root = tmp_path / "cg"
    unit = root / "systemd/system.slice/client.service"
    unit.mkdir(parents=True)
    (unit / "cgroup.procs").write_text("4242\n")
    result = _run_stop(tmp_path, root)
    assert result.returncode == 1
    assert "live processes" in result.stderr


def test_stop_refuses_when_v1_pseudo_file_lists_a_pid(tmp_path):
    """The real kernel case: st_size is 0 but the file lists a process.

    This is the case the previous implementation missed. `-s` tests st_size,
    which is always 0 for cgroup.procs, so a guard written as `[ -s ... ]`
    passed on every populated group. Only reading the content can tell.
    """
    root = tmp_path / "cg"
    unit = root / "systemd/system.slice/client.service"
    unit.mkdir(parents=True)
    _pseudo_file(unit / "cgroup.procs", "4242\n")
    result = _run_stop(tmp_path, root)
    assert result.returncode == 1
    assert "live processes" in result.stderr


def test_stop_allows_empty_v1_pseudo_file(tmp_path):
    """An empty group reads as empty, not as unreadable."""
    root = tmp_path / "cg"
    unit = root / "systemd/system.slice/client.service"
    unit.mkdir(parents=True)
    _pseudo_file(unit / "cgroup.procs", "")
    result = _run_stop(tmp_path, root)
    assert result.returncode == 0
    assert "unreadable" not in result.stderr


def test_stop_allows_empty_v1_group(tmp_path):
    root = tmp_path / "cg"
    unit = root / "systemd/system.slice/client.service"
    unit.mkdir(parents=True)
    (unit / "cgroup.procs").write_text("")
    result = _run_stop(tmp_path, root)
    assert result.returncode == 0


def test_stop_refuses_when_v2_group_is_populated(tmp_path):
    root = tmp_path / "cg"
    root.mkdir()
    (root / "cgroup.controllers").write_text("cpu\n")
    unit = root / "system.slice/client.service"
    unit.mkdir(parents=True)
    (unit / "cgroup.events").write_text("populated 1\nfrozen 0\n")
    result = _run_stop(tmp_path, root)
    assert result.returncode == 1
    assert "not confirmed empty" in result.stderr


def test_stop_allows_empty_v2_group(tmp_path):
    root = tmp_path / "cg"
    root.mkdir()
    (root / "cgroup.controllers").write_text("cpu\n")
    unit = root / "system.slice/client.service"
    unit.mkdir(parents=True)
    (unit / "cgroup.events").write_text("populated 0\nfrozen 0\n")
    result = _run_stop(tmp_path, root)
    assert result.returncode == 0


def test_stop_warns_when_hierarchy_is_unrecognisable(tmp_path):
    root = tmp_path / "cg"
    root.mkdir()
    result = _run_stop(tmp_path, root)
    assert result.returncode == 0
    assert "cannot determine the cgroup hierarchy" in result.stderr


def test_unreadable_procs_refuses(tmp_path):
    """An unreadable file is not an empty one.

The requirement is explicit: a read failure must not be treated as an empty
    group. Refusing keeps the failure visible; passing would replace files under
    processes that are still running.
    """
    root = tmp_path / "cg"
    unit = root / "systemd/system.slice/client.service"
    unit.mkdir(parents=True)
    (unit / "cgroup.procs").mkdir()
    result = _run_stop(tmp_path, root)
    assert result.returncode == 1
    assert "unreadable" in result.stderr


def test_missing_procs_file_refuses_rather_than_assuming_empty(tmp_path):
    """A directory without cgroup.procs is abnormal, not an empty group.

    Under v1 every cgroup directory carries cgroup.procs. Its absence means the
    mount does not show this unit the way systemd describes it, so the answer is
    unknown. Treating unknown as empty would replace files under processes we
    could not see. A host whose controller mount does not carry the unit at all
    has no directory, which is handled separately as "nothing to verify".
    """
    root = tmp_path / "cg"
    (root / "systemd/system.slice/client.service").mkdir(parents=True)
    result = _run_stop(tmp_path, root)
    assert result.returncode == 1
    assert "unreadable" in result.stderr
    assert "live processes" not in result.stderr


def test_stop_reads_procs_content_not_just_presence(tmp_path):
    """A pid in cgroup.procs means a live member; nothing there means empty."""
    root = tmp_path / "cg"
    unit = root / "systemd/system.slice/client.service"
    unit.mkdir(parents=True)
    (unit / "cgroup.procs").write_text("")
    assert _run_stop(tmp_path, root).returncode == 0
    (unit / "cgroup.procs").write_text("9999\n")
    assert _run_stop(tmp_path, root).returncode == 1


def test_generated_hooks_are_shell_valid(tmp_path, monkeypatch):
    """All four script bodies must survive sh -n with the new branching."""
    tree = tmp_path / "tree"
    binary = tree / "runtime/bin/python3"
    binary.parent.mkdir(parents=True)
    binary.write_text("fixture")
    monkeypatch.setattr(module.subprocess, "run", lambda argv, **kwargs: None)
    module.build(tree, tmp_path / "build", "0.4.3")
    for name in ("preinst", "prerm", "postinst", "postrm"):
        path = tmp_path / "build/root/DEBIAN" / name
        subprocess.run(["sh", "-n", str(path)], check=True)
