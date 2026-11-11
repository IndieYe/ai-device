"""Temporary-tree native writer experiment; no installer or service execution."""

import fcntl
import platform
import select
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    (platform.system(), platform.machine()) != ("Darwin", "arm64"), reason="Darwin native probe"
)


@pytest.fixture
def probe(tmp_path):
    executable = tmp_path / "probe"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-arch",
            "arm64",
            str(Path(__file__).parents[1] / "native/client-macos-writer-probe.c"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
    )
    return executable


@pytest.fixture
def trees(tmp_path):
    source, dest, locks = [tmp_path / name for name in ("source", "dest", "locks")]
    for path in (source, dest, locks):
        path.mkdir(mode=0o700)
    (source / "version").write_text("B")
    (source / "new").write_bytes(b"new bytes")
    (source / "alias").symlink_to("version")
    for name in ("management.lock", "start.lock", "runtime.lock"):
        (locks / name).touch(mode=0o600)
    return source, dest, locks


def invoke(probe, trees, fault="none"):
    return subprocess.run(
        [str(probe), *map(str, trees), fault, "no-pause"],
        capture_output=True,
        check=False,
        timeout=10,
    )


def old_tree(dest):
    current = dest / "current"
    current.mkdir(mode=0o700)
    (current / "version").write_text("A")
    (current / "old").write_text("removed from B")


@pytest.mark.parametrize("existing", [False, True])
def test_whole_tree_publish_and_old_retention(probe, trees, existing):
    source, dest, _ = trees
    if existing:
        old_tree(dest)
    result = invoke(probe, trees)
    assert result.returncode == 0, result.stderr
    assert (dest / "current/version").read_text() == "B"
    assert (dest / "current/alias").readlink() == Path("version")
    assert not (dest / "current/old").exists()
    stages = list(dest.glob("stage-*"))
    assert len(stages) == int(existing)
    if existing:
        assert (stages[0] / "version").read_text() == "A"
    assert (source / "version").read_text() == "B"


@pytest.mark.parametrize("fault", ["before-copy", "during-copy", "after-copy", "after-swap"])
def test_abrupt_self_exit_leaves_old_or_new_tree_and_releases_locks(probe, trees, fault):
    _, dest, locks = trees
    old_tree(dest)
    result = invoke(probe, trees, fault)
    assert result.returncode == 86, result.stderr
    assert (dest / "current/version").read_text() == ("B" if fault == "after-swap" else "A")
    for path in locks.iterdir():
        with path.open("rb+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # Explicit caller retry, not recovery code: original fault evidence remains.
    assert invoke(probe, trees).returncode == 0
    assert (dest / "current/version").read_text() == "B"


def test_copy_itself_holds_all_locks_until_publication(probe, trees):
    _, dest, locks = trees
    old_tree(dest)
    process = subprocess.Popen(
        [str(probe), *map(str, trees), "none", "pause"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        ready, _, _ = select.select([process.stdout], [], [], 5)
        assert ready and process.stdout.readline() == b"COPYING\n"
        assert (dest / "current/version").read_text() == "A"
        for path in locks.iterdir():
            with path.open("rb+") as stream, pytest.raises(BlockingIOError):
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        process.stdin.write(b"x")
        process.stdin.flush()
    finally:
        # Native poll expires after five seconds even on assertion failure.
        stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    assert (dest / "current/version").read_text() == "B"


@pytest.mark.parametrize("name", ["management.lock", "start.lock", "runtime.lock"])
def test_busy_refuses_before_stage_creation(probe, trees, name):
    _, dest, locks = trees
    with (locks / name).open("rb+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert invoke(probe, trees).returncode == 75
    assert not list(dest.iterdir())


def test_outer_parent_death_does_not_release_native_writer_locks(probe, trees):
    import sys

    _, dest, locks = trees
    old_tree(dest)
    # A pipe controlled by this test survives the disposable parent's exit.
    parent = r"""
import os, subprocess, sys
subprocess.Popen(sys.argv[1:] + ['none', 'pause'], stdin=sys.stdin,
                 stdout=sys.stdout, stderr=sys.stderr)
os._exit(0)
"""
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", parent, str(probe), *map(str, trees)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        ready, _, _ = select.select([process.stdout], [], [], 5)
        assert ready and process.stdout.readline() == b"COPYING\n"
        assert process.wait(timeout=5) == 0
        assert (dest / "current/version").read_text() == "A"
        for path in locks.iterdir():
            with path.open("rb+") as stream, pytest.raises(BlockingIOError):
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        process.stdin.write(b"x")
        process.stdin.flush()
        ready, _, _ = select.select([process.stdout], [], [], 5)
        assert ready and process.stdout.readline() == b"PUBLISHED\n"
        assert (dest / "current/version").read_text() == "B"
    finally:
        # Writer self-times out in five seconds even if an assertion fails.
        process.communicate(timeout=10)


def test_failed_copy_handshake_keeps_current_and_releases_locks(probe, trees):
    _, dest, locks = trees
    old_tree(dest)
    result = subprocess.run(
        [str(probe), *map(str, trees), "none", "pause"],
        input=b"",
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 70
    assert b"copy failed" in result.stderr
    assert (dest / "current/version").read_text() == "A"
    assert list(dest.glob("stage-*"))
    for path in locks.iterdir():
        with path.open("rb+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_current_symlink_is_not_followed_or_replaced(probe, trees, tmp_path):
    _, dest, _ = trees
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (elsewhere / "version").write_text("unrelated")
    (dest / "current").symlink_to(elsewhere)
    result = invoke(probe, trees)
    assert result.returncode == 70
    assert (dest / "current").is_symlink()
    assert (elsewhere / "version").read_text() == "unrelated"


@pytest.mark.parametrize("fault", ["none", "during-copy", "after-swap"])
def test_stable_lock_inodes_survive_success_and_fault(probe, trees, fault):
    _, dest, locks = trees
    old_tree(dest)
    before = {path.name: (path.stat().st_ino, path.read_bytes()) for path in locks.iterdir()}
    assert invoke(probe, trees, fault).returncode == (0 if fault == "none" else 86)
    assert before == {
        path.name: (path.stat().st_ino, path.read_bytes()) for path in locks.iterdir()
    }


@pytest.mark.parametrize(
    "kind", ["fifo", "public", "hardlink", "escape", "broken", "directory-link"]
)
def test_native_source_validation_refuses_before_copy(probe, trees, kind, tmp_path):
    import os

    source, dest, _ = trees
    old_tree(dest)
    if kind == "fifo":
        os.mkfifo(source / "bad", 0o600)
    elif kind == "public":
        (source / "version").chmod(0o666)
    elif kind == "hardlink":
        os.link(source / "version", source / "bad")
    elif kind == "broken":
        (source / "bad").symlink_to("missing")
    elif kind == "directory-link":
        (source / "bad").symlink_to(".")
    else:
        (tmp_path / "outside").write_text("unrelated")
        (source / "bad").symlink_to("../outside")
    result = invoke(probe, trees)
    assert result.returncode == 70
    assert b"source tree unsupported" in result.stderr
    assert not list(dest.glob("stage-*"))
    assert (dest / "current/version").read_text() == "A"


@pytest.mark.parametrize("change", ["bytes", "mode", "extra"])
def test_native_stage_mismatch_never_publishes(probe, trees, change):
    _, dest, _ = trees
    old_tree(dest)
    process = subprocess.Popen(
        [str(probe), *map(str, trees), "none", "pause"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        ready, _, _ = select.select([process.stdout], [], [], 5)
        assert ready and process.stdout.readline() == b"COPYING\n"
        stage = next(dest.glob("stage-*"))
        if change == "extra":
            (stage / "extra").write_bytes(b"extra")
        else:
            file = next(p for p in stage.iterdir() if p.is_file() and not p.is_symlink())
            if change == "bytes":
                file.write_bytes(b"changed staged content")
            else:
                file.chmod(0o755)
        process.stdin.write(b"x")
        process.stdin.flush()
    finally:
        _, stderr = process.communicate(timeout=10)
    assert process.returncode == 70, stderr
    assert b"stage verification failed" in stderr
    assert (dest / "current/version").read_text() == "A"


def test_native_gate_lease_exit_then_native_publication(probe, trees):
    import json
    import os
    import sys
    import threading

    from test_client_macos_lease_process import RUNTIME_CHILD

    from device_runtime.client_macos_lease import request_original_exit
    from device_runtime.client_macos_lock import MacLockBusy
    from device_runtime.client_macos_start_gate import startup_handoff
    from device_runtime.maintenance import idle_lease

    _, dest, locks = trees
    old_tree(dest)
    runtime = subprocess.Popen(
        [sys.executable, "-c", RUNTIME_CHILD, str(locks)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    reaper = threading.Thread(target=runtime.wait)
    reaper.start()
    writer = None
    try:
        assert select.select([runtime.stdout], [], [], 5)[0]
        assert json.loads(runtime.stdout.readline())["pid"] == runtime.pid
        writer = subprocess.Popen(
            [str(probe), *map(str, trees), "none", "stop"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert select.select([writer.stdout], [], [], 5)[0]
        assert writer.stdout.readline() == b"GATED\n"
        with pytest.raises(MacLockBusy), startup_handoff(locks, uid=os.getuid()):
            pytest.fail("successor crossed native gate")
        with idle_lease(locks / "maintenance.sock") as lease:
            assert request_original_exit(lease, timeout=5).pid == runtime.pid
        assert json.loads((locks / "runtime-result.json").read_text()) == [
            "socket_closed",
            "executor_closed",
            "journal_closed",
        ]
        assert (dest / "current/version").read_text() == "A"
        writer.stdin.write(b"x")
        writer.stdin.flush()
        _, stderr = writer.communicate(timeout=10)
        assert writer.returncode == 0, stderr
        assert (dest / "current/version").read_text() == "B"
    finally:
        if writer is not None:
            writer.communicate(timeout=10)
        reaper.join(timeout=10)
        assert not reaper.is_alive()
        runtime.stdout.close()
        runtime.stderr.close()


def test_native_coordination_eof_never_writes(probe, trees):
    _, dest, _ = trees
    old_tree(dest)
    result = subprocess.run(
        [str(probe), *map(str, trees), "none", "stop"],
        input=b"",
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 70
    assert not list(dest.glob("stage-*"))
    assert (dest / "current/version").read_text() == "A"


@pytest.mark.parametrize("fault", ["before-copy", "during-copy", "after-copy", "after-swap"])
def test_lost_writer_result_observes_bytes_not_exit_status(probe, trees, fault):
    import importlib.util

    script = Path(__file__).parents[1] / "scripts/client-macos-resource-manifest.py"
    spec = importlib.util.spec_from_file_location("writer_observation", script)
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    source, dest, _ = trees
    old_tree(dest)
    prior = observer.inventory(dest / "current")
    target = observer.inventory(source)
    result = invoke(probe, trees, fault)
    assert result.returncode == 86
    evidence = observer.observe_publication(dest / "current", target, prior_manifest=prior)
    assert evidence == {
        "tree": "target_exact" if fault == "after-swap" else "prior_exact",
        "ready": "not_checked",
    }


@pytest.mark.parametrize("mutation", ["none", "source-after-build"])
def test_same_process_writer_uses_embedded_inventory(trees, tmp_path, mutation):
    import importlib.util

    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("embedded_inventory_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source, dest, _ = trees
    old_tree(dest)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), scripts, build)
    writer = tmp_path / "bound-writer"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-arch",
            "arm64",
            "-DEMBEDDED_RESOURCE_CHECK",
            "-I",
            str(build),
            str(Path(__file__).parents[1] / "native/client-macos-writer-probe.c"),
            "-o",
            str(writer),
        ],
        check=True,
        capture_output=True,
    )
    if mutation == "source-after-build":
        (source / "version").write_text("different bytes after inventory compilation")
    result = invoke(writer, trees)
    if mutation == "none":
        assert result.returncode == 0, result.stderr
        assert (dest / "current/version").read_text() == "B"
    else:
        assert result.returncode == 70
        assert b"source differs from embedded inventory" in result.stderr
        assert (dest / "current/version").read_text() == "A"
        assert not list(dest.glob("stage-*"))


def test_no_argument_bound_writer_uses_only_sibling_paths(trees, tmp_path):
    import importlib.util
    import shutil

    source, _, _ = trees
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("bound_writer_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    sandbox = tmp_path / "bound"
    sandbox.mkdir(mode=0o700)
    (sandbox / "resources").mkdir()
    shutil.copytree(source, sandbox / "resources/client-tree", symlinks=True)
    for name in ("offline-destination", "offline-locks"):
        (sandbox / name).mkdir(mode=0o700)
    for name in ("management.lock", "start.lock", "runtime.lock"):
        (sandbox / "offline-locks" / name).touch(mode=0o600)
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), sandbox, build)
    executable = sandbox / "bound-writer"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-arch",
            "arm64",
            "-DEMBEDDED_RESOURCE_CHECK",
            "-DOFFLINE_BOUND_WRITER",
            "-I",
            str(build),
            str(Path(__file__).parents[1] / "native/client-macos-writer-probe.c"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
    )
    result = subprocess.run([str(executable)], check=False, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert (sandbox / "offline-destination/current/version").read_text() == "B"
    result = subprocess.run(
        [str(executable), str(source)], check=False, capture_output=True, timeout=10
    )
    assert result.returncode == 70


@pytest.mark.parametrize("fault", ["before-copy", "after-swap"])
def test_embedded_native_observation_does_not_replay_or_claim_ready(trees, tmp_path, fault):
    import importlib.util

    source, dest, _ = trees
    old_tree(dest)
    script = Path(__file__).parents[1] / "scripts/build-client-nopayload-inspection.py"
    spec = importlib.util.spec_from_file_location("native_observer_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    build = tmp_path / "compile"
    builder.compile_resource_check(builder.INVENTORY.inventory(source), tmp_path, build)
    executable = tmp_path / "embedded-writer"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-arch",
            "arm64",
            "-DEMBEDDED_RESOURCE_CHECK",
            "-I",
            str(build),
            str(Path(__file__).parents[1] / "native/client-macos-writer-probe.c"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
    )
    assert invoke(executable, trees, fault).returncode == 86
    before = sorted(p.name for p in dest.iterdir())
    result = invoke(executable, trees, "observe")
    assert result.returncode == (0 if fault == "after-swap" else 76)
    assert (b"READY_NOT_CHECKED" in result.stdout) == (fault == "after-swap")
    assert sorted(p.name for p in dest.iterdir()) == before


def test_native_lock_owner_is_explicit_not_effective_admin(trees, tmp_path):
    source = Path(__file__).parents[1] / "native/client-macos-writer-probe.c"
    harness = tmp_path / "owner-harness.c"
    harness.write_text(
        "#define main writer_probe_main\n"
        f'#include "{source}"\n'
        "#undef main\n"
        "int main(int argc, char **argv) {\n"
        " if (argc != 2) return 64;\n"
        ' int bad = acquire(argv[1], "runtime.lock", getuid()+1);\n'
        " if (bad != -1) return 81;\n"
        ' int good = acquire(argv[1], "runtime.lock", getuid());\n'
        " if (good < 0) return 82;\n"
        " close(good); return 0;\n}\n"
    )
    executable = tmp_path / "owner-harness"
    subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-arch",
            "arm64",
            str(harness),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [str(executable), str(trees[2])], check=False, capture_output=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
