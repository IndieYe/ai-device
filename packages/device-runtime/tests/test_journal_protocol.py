import asyncio
import time

import pytest
from pydantic import ValidationError

from device_runtime.client import device_url
from device_runtime.journal import Conflict, Journal
from device_runtime.protocol import Operation


def operation(**changes):
    return Operation.model_validate(
        {
            "operation_id": "op-one",
            "device_id": "laptop-one",
            "action": "fs.write_file",
            "arguments": {"path": "/tmp/device-only", "content": "x"},
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"device_id": ""},
        {"operation_id": ""},
        {"device_id": "a/b"},
        {"action": "bash.script"},
        {"arguments": {"path": "relative", "content": ""}},
        {"arguments": {"path": "/tmp/f", "content": "", "host": "server"}},
        {
            "action": "bash.execute",
            "arguments": {"mode": "run", "argv": ["echo"], "cwd": "/tmp", "timeout": 0},
        },
        {"action": "bash.execute", "arguments": {"mode": "run", "argv": ["echo\0"], "cwd": "/tmp"}},
        {"arguments": {"path": "/tmp/f", "content": "😃" * 262144}},
    ],
)
def test_invalid_operations(changes):
    with pytest.raises(ValidationError):
        operation(**changes)


@pytest.mark.parametrize(
    "url",
    [
        "ws://example.com",
        "https://example.com",
        "wss://user:token@example.com",
        "ws://localhost.evil",
        "wss://example.com/?token=x",
    ],
)
def test_client_transport_boundary(url):
    with pytest.raises(ValueError):
        device_url(url, "laptop-one")


def test_loopback_and_tls_supported():
    assert device_url("ws://127.0.0.1:99", "one").endswith("/one/connect")
    assert device_url("wss://example.com", "one").startswith("wss://")


async def test_idempotency_and_conflict(tmp_path):
    journal = Journal(tmp_path / "ops.sqlite3")
    await journal.initialize()
    try:
        _, fresh = await journal.accept(operation(), "sent")
        assert fresh
        _, fresh = await journal.accept(operation(), "sent")
        assert not fresh
        with pytest.raises(Conflict):
            await journal.accept(
                operation(arguments={"path": "/tmp/another", "content": "x"}), "sent"
            )
    finally:
        await journal.close()


async def test_restart_unknown_terminal_immutable_and_ack_generation(tmp_path):
    path = tmp_path / "ops.sqlite3"
    journal = Journal(path)
    await journal.initialize()
    await journal.accept(operation(), "running")
    await journal.close()
    journal = Journal(path)
    await journal.initialize()
    try:
        assert (await journal.get("op-one"))["state"] == "unknown"
        result = {"data": {"success": True}}
        await journal.update("op-one", "succeeded", result)
        await journal.confirm("op-one", "running")
        assert not (await journal.get("op-one"))["confirmed"]
        await journal.confirm("op-one", "succeeded")
        assert (await journal.get("op-one"))["confirmed"]
        with pytest.raises(Conflict):
            await journal.update("op-one", "failed", result)
        await journal.update("op-one", "succeeded", result)
    finally:
        await journal.close()
    journal = Journal(path)
    await journal.initialize()
    assert (await journal.get("op-one"))["state"] == "succeeded"
    await journal.close()


async def test_state_directory_lease_and_identity(tmp_path):
    path = tmp_path / "ops.sqlite3"
    first = Journal(path, owner="device-one")
    await first.initialize()
    second = Journal(path, owner="device-one")
    with pytest.raises(BlockingIOError):
        await second.initialize()
    await first.close()
    second = Journal(path, owner="device-two")
    with pytest.raises(Conflict):
        await second.initialize()
    await first.initialize()  # failed identity check must not leak lease
    await first.close()


async def test_journal_work_does_not_block_event_loop(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "ops.sqlite3")
    await journal.initialize()
    original = journal._connect

    def slow():
        time.sleep(0.15)
        return original()

    monkeypatch.setattr(journal, "_connect", slow)
    pending = asyncio.create_task(journal.get("op-one"))
    await asyncio.sleep(0.01)
    assert not pending.done()
    assert await pending is None
    await journal.close()


@pytest.mark.parametrize("state", ["dispatching", "sent", "running"])
async def test_center_restart_never_dispatches_persisted_nonterminal(tmp_path, state):
    import httpx
    from conftest import ADMIN, TOKEN

    from device_runtime.server import create_app

    path = tmp_path / "operations.sqlite3"
    old = Journal(path)
    await old.initialize()
    await old.accept(operation(), state)
    await old.close()  # represent process loss, without graceful disconnect finalization
    app = create_app(tmp_path, admin_token=ADMIN, device_tokens={"laptop-one": TOKEN})
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer " + ADMIN},
        ) as http,
    ):
        record = (await http.get("/v1/operations/op-one")).json()
        assert record["state"] == "unknown"
        duplicate = await http.post("/v1/operations", json=operation().model_dump())
        assert duplicate.status_code == 200 and duplicate.json()["state"] == "unknown"
        assert not app.state.device_service.peers


async def test_repeated_cancellation_waits_for_transaction_before_releasing_lease(
    tmp_path, monkeypatch
):
    import threading

    journal = Journal(tmp_path / "ops.sqlite3")
    await journal.initialize()
    original = journal._connect
    started = threading.Event()
    release = threading.Event()

    def paused():
        started.set()
        release.wait(5)
        return original()

    monkeypatch.setattr(journal, "_connect", paused)
    pending = asyncio.create_task(journal.accept(operation(), "running"))
    await asyncio.to_thread(started.wait, 5)
    pending.cancel()
    await asyncio.sleep(0.01)
    pending.cancel()
    assert not pending.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert (await journal.get("op-one"))["state"] == "running"
    await journal.close()
