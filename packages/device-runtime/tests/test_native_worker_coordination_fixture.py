"""Isolated coordination experiment, not a production worker or recovery protocol.

Only cooperating fixture processes use this lease; systemd, old workers and
external actions do not participate. A visible record is not durability proof.
"""

import fcntl
import multiprocessing
import os
import queue
from pathlib import Path

import pytest


def _sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(path.parent)


def _fixture_worker(directory, phase, entered, release, messages):
    directory = Path(directory)
    with (directory / "lease").open("r+b") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            messages.put("busy")
            return
        if (directory / "intent").exists():
            messages.put("occupied")
            return
        _write_new(directory / "intent", b"fixture-operation-v1")
        entered.set()
        if not release.wait(10):
            messages.put("release_timeout")
            return
        if phase == "crash_after_intent":
            os._exit(71)
        _write_new(directory / "effect", b"one fixture effect")
        if phase == "crash_after_effect":
            os._exit(72)
        _write_new(directory / "terminal", b"fixture-complete-v1")
        messages.put("complete")


def _run_contender(ctx, directory):
    entered, release, messages = ctx.Event(), ctx.Event(), ctx.Queue()
    contender = ctx.Process(target=_fixture_worker, args=(directory, "complete", entered, release, messages))
    contender.start()
    contender.join(10)
    assert not contender.is_alive()
    assert contender.exitcode == 0
    try:
        return messages.get(timeout=3)
    except queue.Empty:
        pytest.fail("contender returned no result")
    finally:
        messages.close()
        messages.join_thread()


@pytest.mark.parametrize("phase", ["complete", "crash_after_intent", "crash_after_effect"])
def test_cooperating_workers_refuse_overlap_and_replay(tmp_path, phase):
    ctx = multiprocessing.get_context("spawn")
    (tmp_path / "lease").touch()
    entered, release, messages = ctx.Event(), ctx.Event(), ctx.Queue()
    worker = ctx.Process(target=_fixture_worker, args=(tmp_path, phase, entered, release, messages))
    worker.start()
    try:
        assert entered.wait(10)
        assert _run_contender(ctx, tmp_path) == "busy"
        assert not (tmp_path / "effect").exists()
        release.set()
        worker.join(10)
        assert not worker.is_alive()
        assert worker.exitcode == {"complete": 0, "crash_after_intent": 71, "crash_after_effect": 72}[phase]
        assert _run_contender(ctx, tmp_path) == "occupied"
        assert (tmp_path / "intent").read_bytes() == b"fixture-operation-v1"
        assert (tmp_path / "effect").exists() == (phase != "crash_after_intent")
        assert (tmp_path / "terminal").exists() == (phase == "complete")
        if phase == "complete":
            assert messages.get(timeout=3) == "complete"
    finally:
        release.set()
        worker.join(10)
        assert not worker.is_alive(), "fixture worker did not finish"
        messages.close()
        messages.join_thread()


def _external_fixture(directory, ready, release):
    ready.set()
    if release.wait(10):
        (Path(directory) / "external-effect").write_bytes(b"external work completed")


def test_worker_crash_does_not_stop_nonparticipating_external_work(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    (tmp_path / "lease").touch()
    external_ready, external_release = ctx.Event(), ctx.Event()
    external = ctx.Process(target=_external_fixture, args=(tmp_path, external_ready, external_release))
    entered, release, messages = ctx.Event(), ctx.Event(), ctx.Queue()
    worker = ctx.Process(target=_fixture_worker, args=(tmp_path, "crash_after_intent", entered, release, messages))
    external.start()
    worker.start()
    try:
        assert external_ready.wait(10) and entered.wait(10)
        release.set()
        worker.join(10)
        assert worker.exitcode == 71
        assert _run_contender(ctx, tmp_path) == "occupied"
        assert external.is_alive() and not (tmp_path / "external-effect").exists()
        external_release.set()
        external.join(10)
        assert external.exitcode == 0
        assert (tmp_path / "external-effect").read_bytes() == b"external work completed"
        assert not (tmp_path / "terminal").exists()
    finally:
        release.set()
        external_release.set()
        worker.join(10)
        external.join(10)
        assert not worker.is_alive() and not external.is_alive()
        messages.close()
        messages.join_thread()
