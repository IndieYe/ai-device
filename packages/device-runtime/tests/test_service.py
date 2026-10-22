import asyncio
from contextlib import asynccontextmanager

import pytest
from test_journal_protocol import operation

from device_runtime.journal import Conflict, Journal
from device_runtime.protocol import Hello, Report
from device_runtime.service import DeviceService, Peer, ServiceError


class Socket:
    def __init__(self, *, fail=False, paused=False):
        self.sent = []
        self.fail = fail
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not paused:
            self.release.set()

    async def send_json(self, value):
        self.started.set()
        await self.release.wait()
        if self.fail:
            raise OSError("disconnected")
        self.sent.append(value)

    async def close(self, **kwargs):
        pass


@pytest.fixture
async def setup_service(tmp_path):
    journal = Journal(tmp_path / "ops.sqlite3")
    await journal.initialize()
    service = DeviceService(journal, ["laptop-one"])
    try:
        yield service
    finally:
        await service.close()
        await journal.close()


def peer(socket=None, capabilities=None):
    return Peer(
        socket or Socket(),
        Hello(v=1, type="hello", platform="linux", capabilities=capabilities or ["fs.write_file"]),
    )


async def test_offline_is_not_queued(setup_service):
    service = setup_service
    with pytest.raises(ServiceError, match="offline"):
        await service.submit(operation())
    assert await service.journal.get("op-one") is None


async def test_target_capability_idempotency_and_conflict(setup_service):
    service = setup_service
    current = peer()
    await service.attach("laptop-one", current)
    first = await service.submit(operation())
    second = await service.submit(operation())
    assert first == second and len(current.websocket.sent) == 1
    with pytest.raises(ServiceError, match="offline"):
        await service.submit(operation(operation_id="other", device_id="not-local"))
    with pytest.raises(ServiceError, match="capability"):
        await service.submit(
            operation(
                operation_id="other", action="fs.read_file", arguments={"path": "/tmp/only-device"}
            )
        )
    with pytest.raises(Conflict):
        await service.submit(operation(device_id="different"))


async def test_send_unknown_is_never_resent(setup_service):
    service = setup_service
    current = peer(Socket(fail=True))
    await service.attach("laptop-one", current)
    assert (await service.submit(operation()))["state"] == "unknown"
    current.websocket.fail = False
    assert (await service.submit(operation()))["state"] == "unknown"
    assert not current.websocket.sent


async def test_stale_disconnect_does_not_detach_current_peer(setup_service):
    service = setup_service
    current = peer()
    await service.attach("laptop-one", current)
    await service.submit(operation())
    await service.detach("laptop-one", peer())
    assert service.peers["laptop-one"] is current
    await service.detach("laptop-one", current)
    assert (await service.journal.get("op-one"))["state"] == "unknown"


async def test_cancelled_http_request_retains_owned_submission(setup_service):
    service = setup_service
    socket = Socket(paused=True)
    await service.attach("laptop-one", peer(socket))
    caller = asyncio.create_task(service.submit(operation()))
    await socket.started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    socket.release.set()
    await service.close()
    assert len(socket.sent) == 1
    assert (await service.journal.get("op-one"))["state"] == "unknown"


async def test_report_device_and_digest_boundaries(setup_service):
    service = setup_service
    current = peer()
    await service.attach("laptop-one", current)
    op = operation()
    await service.submit(op)
    report = Report(
        v=1, type="report", operation_id=op.operation_id, digest="0" * 64, state="running"
    )
    with pytest.raises(ServiceError):
        await service.report("laptop-one", current, report)
    report.digest = op.digest
    result = {
        "location": {"namespace": "device", "device_id": op.device_id},
        "data": {"success": True},
    }
    report.state, report.result = "succeeded", result
    await service.report("laptop-one", current, report)
    report.state, report.result = "running", None
    await service.report("laptop-one", current, report)
    await service.detach("laptop-one", current)
    assert (await service.journal.get(op.operation_id))["state"] == "succeeded"


@asynccontextmanager
async def tracked_callers(*coroutines):
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    try:
        yield tasks
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def wait_for_waiters(service, tasks, count=1):
    async with asyncio.timeout(2):
        while sum(map(len, service._waiters.values())) != count:
            for task in tasks:
                if task.done():
                    task.result()
                    raise AssertionError("submission ended before waiter registration")
            await asyncio.sleep(0)


async def finish(service, current, op, state="succeeded"):
    await service.report(
        op.device_id,
        current,
        Report(
            v=1,
            type="report",
            operation_id=op.operation_id,
            digest=op.digest,
            state=state,
            result={"data": {"success": state == "succeeded"}},
        ),
    )


@pytest.mark.parametrize("state", ["succeeded", "failed"])
async def test_wait_returns_persisted_terminal_without_locking_reports(setup_service, state):
    service = setup_service
    current = peer()
    await service.attach("laptop-one", current)
    op = operation()
    async with tracked_callers(service.submit(op, wait_timeout_ms=1000)) as tasks:
        caller = tasks[0]
        await wait_for_waiters(service, [caller])
        await finish(service, current, op, state)
        result = await asyncio.wait_for(caller, 1)
        assert result == await service.journal.get(op.operation_id)
        assert result["state"] == state
        assert not service._waiters


async def test_wait_timeout_does_not_cancel_or_resend(setup_service):
    service = setup_service
    current = peer()
    await service.attach("laptop-one", current)
    op = operation()
    assert (await service.submit(op, wait_timeout_ms=1))["state"] == "sent"
    assert not service._waiters
    await finish(service, current, op)
    assert (await service.submit(op, wait_timeout_ms=100))["state"] == "succeeded"
    assert len(current.websocket.sent) == 1


async def test_multiple_waiters_cancel_independently(setup_service):
    service = setup_service
    current = peer()
    await service.attach("laptop-one", current)
    op = operation()
    async with tracked_callers(
        *(service.submit(op, wait_timeout_ms=1000) for _ in range(2))
    ) as callers:
        await wait_for_waiters(service, callers, 2)
        callers[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await callers[0]
        await finish(service, current, op)
        assert (await callers[1])["state"] == "succeeded"
        assert len(current.websocket.sent) == 1
        assert not service._waiters


@pytest.mark.parametrize("close", [False, True])
async def test_disconnect_or_close_wakes_waiters(setup_service, close):
    service = setup_service
    current = peer()
    await service.attach("laptop-one", current)
    async with tracked_callers(service.submit(operation(), wait_timeout_ms=5000)) as tasks:
        caller = tasks[0]
        await wait_for_waiters(service, [caller])
        if close:
            await service.close()
        else:
            await service.detach("laptop-one", current)
        assert (await asyncio.wait_for(caller, 1))["state"] in {"sent", "unknown"}
        assert not service._waiters


async def test_result_before_wait_registration_is_not_lost(setup_service):
    service = setup_service
    current = peer()
    await service.attach("laptop-one", current)
    op = operation()
    await service.submit(op)
    await finish(service, current, op)
    assert (await service._wait_for_result(op.operation_id, 1000))["state"] == "succeeded"
    assert not service._waiters


@pytest.mark.parametrize("value", [-1, 5001, True, 1.5])
async def test_invalid_wait_is_rejected_before_submission(setup_service, value):
    with pytest.raises(ValueError):
        await setup_service.submit(operation(), wait_timeout_ms=value)
    assert await setup_service.journal.get("op-one") is None


async def test_report_during_wait_read_is_not_lost(setup_service, monkeypatch):
    service = setup_service
    current = peer()
    await service.attach("laptop-one", current)
    op = operation()
    await service.submit(op)
    original = service.journal.get
    captured = asyncio.Event()
    release = asyncio.Event()

    async def delayed_get(operation_id):
        record = await original(operation_id)
        if asyncio.current_task() is caller:
            captured.set()
            await release.wait()
        return record

    monkeypatch.setattr(service.journal, "get", delayed_get)
    async with tracked_callers(service._wait_for_result(op.operation_id, 1000)) as tasks:
        caller = tasks[0]
        await asyncio.wait_for(captured.wait(), 2)
        await finish(service, current, op)
        release.set()
        assert (await asyncio.wait_for(caller, 1))["state"] == "succeeded"
        assert not service._waiters


async def test_wait_does_not_block_other_submission(setup_service):
    service = setup_service
    current = peer()
    await service.attach("laptop-one", current)
    async with tracked_callers(service.submit(operation(), wait_timeout_ms=1000)) as tasks:
        caller = tasks[0]
        await wait_for_waiters(service, [caller])
        other = await asyncio.wait_for(service.submit(operation(operation_id="op-other")), 0.5)
        assert other["state"] == "sent"
        await service.detach("laptop-one", current)
        assert (await caller)["state"] == "unknown"


async def test_waiter_registration_failure_propagates_and_cleans_up(setup_service):
    async def fail():
        raise RuntimeError("registration failed")

    with pytest.raises(RuntimeError, match="registration failed"):
        async with tracked_callers(fail()) as tasks:
            await wait_for_waiters(setup_service, tasks)
    assert tasks[0].done()


async def test_tracked_callers_cleanup_on_test_failure():
    async def pending():
        await asyncio.Event().wait()

    with pytest.raises(AssertionError, match="test failure"):
        async with tracked_callers(pending()) as tasks:
            await asyncio.sleep(0)
            raise AssertionError("test failure")
    assert tasks[0].cancelled()
