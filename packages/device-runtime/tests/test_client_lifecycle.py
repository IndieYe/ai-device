import asyncio

import pytest
from conftest import TOKEN, FakeExecutor
from test_journal_protocol import operation

from device_runtime.client import DeviceClient
from device_runtime.journal import Journal


def client(tmp_path, executor):
    return DeviceClient(
        server_url="ws://127.0.0.1:9999",
        device_id="laptop-one",
        token=TOKEN,
        state_dir=tmp_path,
        executor=executor,
    )


async def test_executor_close_failure_still_recovers_and_releases_journal(tmp_path):
    class BrokenClose(FakeExecutor):
        async def aclose(self):
            raise RuntimeError("intentional test close failure")

    runtime = client(tmp_path, BrokenClose())
    await runtime.journal.initialize()
    runtime._initialized = True
    await runtime.journal.accept(operation(), "running")
    with pytest.raises(RuntimeError, match="intentional"):
        await runtime.close()
    recovered = Journal(tmp_path / "operations.sqlite3", owner=runtime.url)
    await recovered.initialize()
    assert (await recovered.get("op-one"))["state"] == "unknown"
    await recovered.close()


async def test_repeated_cancellation_of_close_waits_for_owned_cleanup(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()

    class SlowClose(FakeExecutor):
        async def aclose(self):
            started.set()
            await release.wait()
            self.closed = True

    executor = SlowClose()
    runtime = client(tmp_path, executor)
    await runtime.journal.initialize()
    runtime._initialized = True
    close = asyncio.create_task(runtime.close())
    await started.wait()
    close.cancel()
    await asyncio.sleep(0.01)
    close.cancel()
    assert not close.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await close
    assert executor.closed
    recovered = Journal(tmp_path / "operations.sqlite3", owner=runtime.url)
    await recovered.initialize()
    await recovered.close()


async def test_restart_unknown_record_does_not_enter_executor(tmp_path):
    executor = FakeExecutor()
    runtime = client(tmp_path, executor)
    await runtime.journal.initialize()
    runtime._initialized = True
    await runtime.journal.accept(operation(), "unknown")
    await runtime._accept(operation())
    assert executor.calls == 0
    await runtime.close()


@pytest.mark.parametrize("status", [403, 503])
async def test_handshake_policy_permanent_pairing_vs_transient_service(
    tmp_path, monkeypatch, status
):
    from websockets.datastructures import Headers
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response

    import device_runtime.client as module

    calls = 0
    attempted = asyncio.Event()

    class Unavailable:
        async def __aenter__(self):
            nonlocal calls
            calls += 1
            attempted.set()
            raise InvalidStatus(
                Response(status_code=status, reason_phrase="test", headers=Headers())
            )

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(module, "connect", lambda *args, **kwargs: Unavailable())
    runtime = client(tmp_path, FakeExecutor())
    runtime.reconnect_delay = 0.01
    if status == 403:
        with pytest.raises(RuntimeError, match="pairing"):
            await runtime.run()
        assert calls == 1
    else:
        task = asyncio.create_task(runtime.run())
        await attempted.wait()
        async with asyncio.timeout(2):
            while calls < 2:
                await asyncio.sleep(0.01)
        await runtime.close()
        await task
        assert calls >= 2
