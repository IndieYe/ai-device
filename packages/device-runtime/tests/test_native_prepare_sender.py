import fcntl
import json
import os
import subprocess
import sys

import pytest

from device_runtime import installation as core
from device_runtime.native_prepare_sender import GUARD, PreparationSender


@pytest.mark.parametrize("code", [0, 3])
def test_real_guard_records_matching_reaped_child(tmp_path, code):
    directory = tmp_path / "senders"
    request = {"fixture": "bound-request"}
    with PreparationSender(directory, request) as sender:
        result = sender.run(
            [sys.executable, "-c", f"raise SystemExit({code})"],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            timeout=10,
        )
    intent = json.loads((directory / "1.intent.json").read_text())
    receipt = json.loads((directory / "1.reaped.json").read_text())
    assert result.returncode == code
    assert receipt["intent"] == intent and intent["request"] == request
    assert receipt["state"] == "direct_child_reaped"
    assert receipt["returncode"] == code and receipt["descendants_confirmed"] is False


def test_guard_timeout_reaps_direct_child(tmp_path):
    with PreparationSender(tmp_path / "senders", {}) as sender:
        result = sender.run(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            timeout=0.05,
        )
    receipt = json.loads((tmp_path / "senders/1.reaped.json").read_text())
    assert result.returncode == receipt["returncode"] == 124
    assert receipt["descendants_confirmed"] is False


def test_sender_context_resets_after_error(tmp_path):
    with pytest.raises(ValueError, match="injected"), core.preparation_sender(object()):
        assert core._PREPARATION_SENDER.get() is not None
        raise ValueError("injected")
    assert core._PREPARATION_SENDER.get() is None


def test_actual_invoke_uses_guard_without_changing_default(tmp_path):
    log = tmp_path / "prepare.log"
    directory = tmp_path / "senders"
    with PreparationSender(directory, {"fixture": True}) as sender, core.preparation_sender(sender):
        core._invoke([sys.executable, "-c", "print('guarded')"], cwd=tmp_path, log=log)
    assert b"guarded" in log.read_bytes()
    assert (directory / "1.reaped.json").is_file()
    assert core._PREPARATION_SENDER.get() is None


def test_guard_inherits_lock_after_callers_descriptor_closes(tmp_path):
    directory = tmp_path / "senders"
    directory.mkdir()
    lock = directory / "execution.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    command = "from pathlib import Path; import time,sys; Path(sys.argv[1]).touch();\nwhile not Path(sys.argv[2]).exists(): time.sleep(.01)"
    guard = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            GUARD,
            str(directory / "1.reaped.json"),
            '{"fixture":true}',
            "null",
            sys.executable,
            "-c",
            command,
            str(ready),
            str(release),
        ],
        pass_fds=(fd,),
    )
    os.close(fd)
    try:
        import time

        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists() and guard.poll() is None
        with lock.open("rb") as lease, pytest.raises(BlockingIOError):
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        release.touch()
        guard.wait(timeout=10)
    assert guard.returncode == 0
    with lock.open("rb") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert (directory / "1.reaped.json").is_file()


@pytest.mark.parametrize("code", [0, 3])
def test_observation_never_authorizes_retry(tmp_path, code):
    from device_runtime.native_prepare_sender import observe_senders

    directory = tmp_path / "senders"
    with PreparationSender(directory, {"request": 1}) as sender:
        sender.run(
            [sys.executable, "-c", f"raise SystemExit({code})"],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            timeout=10,
        )
        assert observe_senders(directory, {"request": 1})["state"] == "sender_busy"
    result = observe_senders(directory, {"request": 1})
    assert result["state"] == "direct_children_reaped_descendants_unconfirmed"
    assert result["retry_allowed"] is False and result["descendants_confirmed"] is False


def test_guard_exit_without_receipt_is_unknown_even_when_lock_free(tmp_path, monkeypatch):
    import time

    import device_runtime.native_prepare_sender as module

    # Test guard launches a finite child then exits abnormally, without killing
    # any process. Lock release alone cannot prove that child has stopped.
    done = tmp_path / "child-done"
    command = "import time,sys; from pathlib import Path; time.sleep(.4); Path(sys.argv[1]).touch()"
    broken = "import subprocess,sys,os; subprocess.Popen(sys.argv[4:]); os._exit(9)"
    monkeypatch.setattr(module, "GUARD", broken)
    directory = tmp_path / "senders"
    with PreparationSender(directory, {}) as sender:
        result = sender.run(
            [sys.executable, "-c", command, str(done)],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            timeout=10,
        )
    assert result.returncode == 9
    assert module.observe_senders(directory, {})["state"] == "unknown"
    assert not (directory / "1.reaped.json").exists()
    deadline = time.monotonic() + 10
    while not done.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert done.exists()
    assert module.observe_senders(directory, {})["retry_allowed"] is False


def test_direct_reaping_does_not_prove_descendant_completion(tmp_path):
    import time

    from device_runtime.native_prepare_sender import observe_senders

    ready = tmp_path / "descendant-ready"
    release = tmp_path / "descendant-release"
    done = tmp_path / "descendant-done"
    descendant = "import sys,time; from pathlib import Path; Path(sys.argv[1]).touch(); deadline=time.monotonic()+5\nwhile not Path(sys.argv[2]).exists() and time.monotonic()<deadline: time.sleep(.01)\nPath(sys.argv[3]).touch()"
    command = "import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,'-c',sys.argv[1],*sys.argv[2:]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); deadline=time.monotonic()+4\nwhile not Path(sys.argv[2]).exists() and time.monotonic()<deadline: time.sleep(.01)"
    directory = tmp_path / "senders"
    try:
        with PreparationSender(directory, {}) as sender:
            sender.run(
                [sys.executable, "-c", command, descendant, str(ready), str(release), str(done)],
                cwd=tmp_path,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                timeout=10,
            )
        assert ready.exists() and not done.exists()
        observation = observe_senders(directory, {})
        assert observation["state"] == "direct_children_reaped_descendants_unconfirmed"
        assert observation["retry_allowed"] is False
    finally:
        release.touch()
        deadline = time.monotonic() + 10
        while not done.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
    assert done.exists()


def test_observation_rejects_mismatched_receipt(tmp_path):
    from device_runtime.native_prepare_sender import observe_senders

    directory = tmp_path / "senders"
    with PreparationSender(directory, {}) as sender:
        sender.run(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            timeout=10,
        )
    path = directory / "1.reaped.json"
    receipt = json.loads(path.read_text())
    receipt["intent"]["request"] = {"different": True}
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="matching"):
        observe_senders(directory, {})


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper semantics")
@pytest.mark.parametrize("detached", [False, True])
def test_linux_tree_guard_waits_for_descendants(tmp_path, detached):
    import time

    from device_runtime.native_prepare_sender import observe_senders

    directory = tmp_path / "tree"
    done = tmp_path / "descendant-done"
    descendant = (
        "import time,sys; from pathlib import Path; time.sleep(.3); Path(sys.argv[1]).touch()"
    )
    command = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],start_new_session={detached!r})"
    with PreparationSender(directory, {"tree": True}, reap_descendants=True) as sender:
        start = time.monotonic()
        result = sender.run(
            [sys.executable, "-c", command, descendant, str(done)],
            cwd=tmp_path,
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            timeout=10,
        )
        assert result.returncode == 0 and done.exists()
        assert time.monotonic() - start >= 0.3
    receipt = json.loads((directory / "1.reaped.json").read_text())
    assert receipt["descendants_confirmed"] is True
    assert receipt["state"] == "process_tree_reaped"
    report = observe_senders(directory, {"tree": True})
    assert report["state"] == "process_trees_reaped"
    assert report["descendants_confirmed"] is True
    assert report["retry_allowed"] is False


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper semantics")
def test_linux_tree_receipt_not_published_while_detached_descendant_busy(tmp_path):
    import time

    from device_runtime.native_prepare_sender import TREE_GUARD, observe_senders

    directory = tmp_path / "tree"
    directory.mkdir()
    ready, release, done = (tmp_path / name for name in ("ready", "release", "done"))
    descendant = "from pathlib import Path; import time,sys; Path(sys.argv[1]).touch(); deadline=time.monotonic()+8\nwhile not Path(sys.argv[2]).exists() and time.monotonic()<deadline: time.sleep(.01)\nPath(sys.argv[3]).touch()"
    command = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',*sys.argv[1:]],start_new_session=True)"
    intent = {"request": {}, "sequence": 1, "barrier": "linux-subreaper-v1"}
    (directory / "1.intent.json").write_text(json.dumps(intent))
    fd = os.open(directory / "execution.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    guard = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            TREE_GUARD,
            str(directory / "1.reaped.json"),
            json.dumps(intent),
            "null",
            sys.executable,
            "-c",
            command,
            descendant,
            str(ready),
            str(release),
            str(done),
        ],
        pass_fds=(fd,),
    )
    os.close(fd)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists() and guard.poll() is None
        assert not (directory / "1.reaped.json").exists()
        assert observe_senders(directory, {})["state"] == "sender_busy"
    finally:
        release.touch()
        guard.wait(timeout=15)
    assert done.exists() and guard.returncode == 0
    assert observe_senders(directory, {})["descendants_confirmed"] is True
