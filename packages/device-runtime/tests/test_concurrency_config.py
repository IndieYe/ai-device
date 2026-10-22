"""Concurrency configuration, with no remote device or real service changes."""

import asyncio
import sys

import pytest
from conftest import TOKEN, FakeExecutor
from test_journal_protocol import operation

from device_runtime.client import DeviceClient
from device_runtime.executor import LocalExecutor
from device_runtime.limits import positive_limit
from device_runtime.protocol import Operation


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "8", None])
def test_limits_reject_non_positive_integers(value):
    with pytest.raises(ValueError, match="positive integers"):
        positive_limit(value)


async def test_client_limits_are_separate_and_release_after_completion(tmp_path):
    executor = FakeExecutor(paused=True)
    runtime = DeviceClient(
        server_url="ws://127.0.0.1:9999",
        device_id="laptop-one",
        token=TOKEN,
        state_dir=tmp_path,
        executor=executor,
        max_concurrent_requests=1,
        max_concurrent_queries=2,
    )
    await runtime.journal.initialize()
    runtime._initialized = True
    try:
        await runtime._accept(operation(operation_id="request-one"))
        await executor.started.wait()
        await runtime._accept(operation(operation_id="request-two"))
        assert (await runtime.journal.get("request-two"))["result"]["data"]["error"] == (
            "client_capacity_exceeded"
        )
        for index in range(3):
            await runtime._accept(
                operation(
                    operation_id=f"query-{index}",
                    action="bash.get",
                    arguments={"execution_id": "request-one"},
                )
            )
        assert (await runtime.journal.get("query-2"))["state"] == "failed"
        assert (await runtime.journal.get("query-0"))["state"] == "running"
        assert (await runtime.journal.get("query-1"))["state"] == "running"
        executor.release.set()
        await asyncio.gather(*runtime._tasks)
        await asyncio.sleep(0)
        await runtime._accept(operation(operation_id="request-three"))
        await asyncio.gather(*runtime._tasks)
        assert (await runtime.journal.get("request-three"))["state"] == "succeeded"
    finally:
        executor.release.set()
        await runtime.close()


@pytest.mark.parametrize("mode", ["run", "bash", "script"])
async def test_unified_execution_uses_configured_task_limit(tmp_path, mode):
    executor = LocalExecutor(tmp_path / "state", "test", max_active_tasks=1)
    started, release = asyncio.Event(), asyncio.Event()

    async def command(_arguments):
        started.set()
        await release.wait()
        return {"success": True, "exit_code": 0}

    executor.tasks.run_command = command

    def execution(identifier):
        return Operation(
            device_id="test",
            operation_id=identifier,
            action="bash.execute",
            arguments={
                "mode": mode,
                "cwd": str(tmp_path),
                **{
                    "run": {"argv": [sys.executable, "-c", "pass"]},
                    "bash": {"command": "true"},
                    "script": {"script": "true"},
                }[mode],
            },
        )

    first = asyncio.create_task(executor(execution("exec-one")))
    try:
        await started.wait()
        _, rejected = await executor(execution("exec-two"))
        assert rejected["data"]["code"] == "TASK_CAPACITY_EXCEEDED"
        release.set()
        await first
        _, accepted = await executor(execution("exec-three"))
        assert accepted["data"]["success"]
    finally:
        release.set()
        await first
        await executor.aclose()


async def test_default_limits_remain_eight(tmp_path):
    runtime = DeviceClient(
        server_url="ws://127.0.0.1:9999",
        device_id="test",
        token=TOKEN,
        state_dir=tmp_path,
        executor=FakeExecutor(),
    )
    assert runtime.max_concurrent_requests == runtime.max_concurrent_queries == 8
    executor = LocalExecutor(tmp_path / "executor", "test")
    assert executor.tasks.max_active_tasks == 8
    await executor.aclose()
    await runtime.close()


@pytest.mark.parametrize(
    "option",
    [
        "--max-concurrent-requests",
        "--max-concurrent-queries",
        "--max-active-tasks",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1", "1.5", "abc"])
def test_cli_rejects_invalid_limits(tmp_path, monkeypatch, capsys, option, value):
    from device_runtime.cli import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "device-runtime",
            "client",
            "--server-url",
            "ws://127.0.0.1:9999",
            "--device-id",
            "test",
            "--state-dir",
            str(tmp_path),
            option,
            value,
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "positive integers" in capsys.readouterr().err


def test_cli_passes_limits_to_runtime(tmp_path, monkeypatch):
    from device_runtime.cli import main

    captured = {}

    def executor(*args, **kwargs):
        captured["executor"] = kwargs
        return object()

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def run(self):
            pass

    monkeypatch.setattr("device_runtime.executor.LocalExecutor", executor)
    monkeypatch.setattr("device_runtime.client.DeviceClient", Client)
    monkeypatch.setenv("DEVICE_RUNTIME_TOKEN", TOKEN)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "device-runtime",
            "client",
            "--server-url",
            "ws://127.0.0.1:9999",
            "--device-id",
            "test",
            "--state-dir",
            str(tmp_path),
            "--max-concurrent-requests",
            "12",
            "--max-concurrent-queries",
            "3",
            "--max-active-tasks",
            "16",
        ],
    )
    main()
    assert captured["executor"] == {"max_active_tasks": 16}
    assert captured["client"]["max_concurrent_requests"] == 12
    assert captured["client"]["max_concurrent_queries"] == 3


@pytest.mark.parametrize(
    "action",
    [
        "bash.run",
        "bash.start_task",
        "bash.get_task",
        "bash.read_task_output",
    ],
)
def test_retired_actions_rejected(action):
    from pydantic import ValidationError

    from device_runtime.protocol import CAPABILITIES

    assert action not in CAPABILITIES
    with pytest.raises(ValidationError):
        Operation(device_id="test", operation_id="retired", action=action, arguments={})


@pytest.mark.parametrize("command", ["run-argv", "start-task", "get-task", "read-task-output"])
def test_retired_cli_rejected(command, monkeypatch):
    from device_runtime.cli import main

    monkeypatch.setattr(sys, "argv", ["device-runtime", "ops", command])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "mode,input",
    [
        ("run", {"argv": ["echo"]}),
        ("bash", {"command": "echo"}),
        ("script", {"script": "echo"}),
    ],
)
def test_default_execution_digest_stable_across_wire(mode, input):
    value = Operation(
        device_id="test",
        operation_id="digest",
        action="bash.execute",
        arguments={"cwd": "/tmp", "mode": mode, **input},
    )
    assert Operation.model_validate_json(value.model_dump_json()).digest == value.digest


@pytest.mark.parametrize("mapping", [{"format_version": 1}, [], None, {"paths": None}])
async def test_malformed_output_map_preserves_execution_state(tmp_path, mapping):
    import hashlib
    import json

    from device_runtime.tasks import OUTPUT_MAP_FILE

    root = tmp_path / "instance"
    executor = LocalExecutor(root / "state" / "client", "test")
    try:
        _, executed = await executor(
            Operation(
                device_id="test",
                operation_id="map-command",
                action="bash.execute",
                arguments={
                    "mode": "run",
                    "argv": [sys.executable, "-c", "print('preserved')"],
                    "cwd": str(tmp_path),
                },
            )
        )
        assert executed["data"]["state"] == "succeeded"
        raw = json.dumps(mapping).encode()
        (root / OUTPUT_MAP_FILE).write_bytes(raw)
        (root / "active.json").write_text(
            json.dumps(
                {
                    "task_output_map_sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        )
        state, queried = await executor(
            Operation(
                device_id="test",
                operation_id="map-query",
                action="bash.get",
                arguments={"execution_id": "map-command"},
            )
        )
        assert state == "succeeded"
        data = queried["data"]
        assert data["state"] == "succeeded" and data["exit_code"] == 0
        assert data["output_path"] == executed["data"]["output_path"]
        assert data["output_unavailable"] and data["output"] == ""
        record = await executor.tasks.journal.get("map-command")
        assert record["state"] == "succeeded"
    finally:
        await executor.aclose()
