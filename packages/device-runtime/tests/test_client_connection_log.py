"""The device log has to distinguish "idle" from "cannot reach the center".

client.log stayed at zero bytes for the whole life of the client, so a failing
upgrade and a healthy idle device looked identical. These tests pin the two
properties that make the log usable: a transition is recorded, and a device
that keeps failing does not write a line per retry.
"""

import asyncio

import pytest
from conftest import TOKEN, FakeExecutor

from device_runtime.client import DeviceClient


def client(tmp_path, executor, **kwargs):
    return DeviceClient(
        server_url="ws://127.0.0.1:9",
        device_id="laptop-one",
        token=TOKEN,
        state_dir=tmp_path,
        executor=executor,
        reconnect_delay=0.01,
        **kwargs,
    )


def test_first_failure_is_logged_even_though_no_connection_ever_succeeded(
    tmp_path, capsys
):
    """A device that never reaches the center must still say so.

    Dedupe on "same as last state" would otherwise start from False and swallow
    the only line the device ever had to write.
    """
    runtime = client(tmp_path, FakeExecutor())
    runtime._log_connection("disconnected: OSError: refused", connected=False)

    assert "disconnected: OSError: refused" in capsys.readouterr().err


def test_repeated_failures_write_one_line(tmp_path, capsys):
    """Retrying forever at one second must not become the log's entire content."""
    runtime = client(tmp_path, FakeExecutor())
    for _ in range(50):
        runtime._log_connection("disconnected: OSError: refused", connected=False)

    lines = capsys.readouterr().err.count("disconnected")
    assert lines == 1


def test_reconnect_after_a_drop_is_logged_again(tmp_path, capsys):
    """The recovery is the line an operator looks for; it must not be deduped."""
    runtime = client(tmp_path, FakeExecutor())
    runtime._log_connection("disconnected: OSError: refused", connected=False)
    runtime._log_connection("connected to ws://center/v1/devices/x/connect", connected=True)
    runtime._log_connection("disconnected: ConnectionClosed: 1006", connected=False)

    captured = capsys.readouterr().err
    assert captured.count("disconnected") == 2
    assert "connected to" in captured


def test_the_token_never_reaches_the_log(tmp_path, capsys):
    """Log text is durable and readable by anyone with the user's home."""
    runtime = client(tmp_path, FakeExecutor())
    runtime._log_connection("connected to ws://center/v1/devices/x/connect", connected=True)
    runtime._log_connection(
        "disconnected: OSError: refused; retrying every 1s", connected=False
    )

    assert TOKEN not in capsys.readouterr().err


@pytest.mark.asyncio
async def test_an_unreachable_center_is_recorded_then_the_client_keeps_retrying(
    tmp_path, capsys
):
    """End to end through run(): the failure is logged once and the loop lives.

    Port 9 is the discard service, so the connect fails immediately and the loop
    goes straight into its retry path. The point is that run() neither exits nor
    stays silent.
    """
    runtime = client(tmp_path, FakeExecutor())
    task = asyncio.create_task(runtime.run())
    await asyncio.sleep(0.3)
    try:
        assert not task.done(), "an unreachable center must not end the client"
        assert "disconnected" in capsys.readouterr().err
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
