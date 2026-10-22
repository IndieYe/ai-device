import asyncio
import threading

import pytest
from conftest import FakeExecutor, running_client
from test_client_lifecycle import client
from test_journal_protocol import operation
from test_websocket import payload

from device_runtime.executor import LocalExecutor
from device_runtime.journal import Journal


async def test_close_waits_for_initialization_and_releases_lease(tmp_path, monkeypatch):
    runtime = client(tmp_path, FakeExecutor())
    started, release = asyncio.Event(), asyncio.Event()
    original = runtime.journal.initialize

    async def delayed():
        await original()
        started.set()
        await release.wait()

    monkeypatch.setattr(runtime.journal, "initialize", delayed)
    run = asyncio.create_task(runtime.run())
    await started.wait()
    close = asyncio.create_task(runtime.close())
    try:
        await asyncio.sleep(0.03)
        assert not close.done(), "close must join initialization before releasing the lease"
    finally:
        release.set()
        await asyncio.wait_for(close, 5)
        await asyncio.wait_for(run, 5)
        try:
            assert runtime.journal._lease is None, (
                "run/close must not leave an initialization lease"
            )
        finally:
            # Do not leak a defective baseline lease into subsequent tests.
            await runtime.journal.close()
    recovered = Journal(tmp_path / "operations.sqlite3", owner=runtime.url)
    await recovered.initialize()
    await recovered.close()


async def test_close_waits_for_acceptance_and_never_starts_executor(tmp_path, monkeypatch):
    executor = FakeExecutor()
    runtime = client(tmp_path, executor)
    await runtime.journal.initialize()
    runtime._initialized = True
    started, release = asyncio.Event(), asyncio.Event()
    original = runtime.journal.accept

    async def delayed(*args, **kwargs):
        record = await original(*args, **kwargs)
        started.set()
        await release.wait()
        return record

    monkeypatch.setattr(runtime.journal, "accept", delayed)
    acceptance = asyncio.create_task(runtime._accept(operation()))
    await started.wait()
    close = asyncio.create_task(runtime.close())
    try:
        await asyncio.sleep(0.03)
        assert not close.done(), "close must join the in-flight acceptance path"
    finally:
        release.set()
        await asyncio.wait_for(acceptance, 5)
        await asyncio.wait_for(close, 5)
        await asyncio.gather(*tuple(runtime._tasks), return_exceptions=True)
    assert executor.calls == 0
    assert not runtime._tasks
    recovered = Journal(tmp_path / "operations.sqlite3", owner=runtime.url)
    await recovered.initialize()
    assert (await recovered.get("op-one"))["state"] == "unknown"
    await recovered.close()


async def test_live_close_during_acceptance_never_executes(live_server, tmp_path, monkeypatch):
    _, _, http = live_server
    executor = FakeExecutor()
    started, release = asyncio.Event(), asyncio.Event()
    async with running_client(live_server, tmp_path / "client", executor) as runtime:
        original = runtime.journal.accept

        async def delayed(*args, **kwargs):
            record = await original(*args, **kwargs)
            started.set()
            await release.wait()
            return record

        monkeypatch.setattr(runtime.journal, "accept", delayed)
        await http.post("/v1/operations", json=payload())
        await asyncio.wait_for(started.wait(), 5)
        close = asyncio.create_task(runtime.close())
        try:
            await asyncio.sleep(0.03)
            assert not close.done()
        finally:
            release.set()
            await asyncio.wait_for(close, 5)
    assert executor.calls == 0
    assert (await http.get("/v1/operations/op-net")).json()["state"] == "unknown"


@pytest.mark.parametrize("fails", [False, True])
async def test_fs_worker_drains_before_lease_release(live_server, tmp_path, fails):
    _, _, http = live_server
    state = tmp_path / "client"
    executor = LocalExecutor(state, "laptop-one")
    started, release = threading.Event(), threading.Event()
    original = executor.files["fs.write_file"]
    target = tmp_path / "late-file"

    def delayed(**arguments):
        started.set()
        release.wait(5)
        if fails:
            raise OSError("intentional worker failure")
        return original(**arguments)

    executor.files["fs.write_file"] = delayed
    async with running_client(live_server, state, executor) as runtime:
        await http.post(
            "/v1/operations", json=payload(arguments={"path": str(target), "content": "x"})
        )
        assert await asyncio.to_thread(started.wait, 5)
        close = asyncio.create_task(runtime.close())
        competitor = Journal(state / "operations.sqlite3", owner=runtime.url)
        try:
            await asyncio.sleep(0.03)
            assert not close.done(), "uncancellable FS work must finish before close returns"
            with pytest.raises(BlockingIOError):
                await competitor.initialize()
        finally:
            release.set()
            await asyncio.wait_for(close, 5)
            await competitor.close()
        assert target.exists() is (not fails)
        recovered = Journal(state / "operations.sqlite3", owner=runtime.url)
        await recovered.initialize()
        assert (await recovered.get("op-net"))["state"] == "unknown"
        await recovered.close()
