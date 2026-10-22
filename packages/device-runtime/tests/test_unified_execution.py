"""Unified execution contracts, using isolated local processes only."""

import asyncio
import sys

import pytest

from device_runtime.executor import LocalExecutor
from device_runtime.protocol import Operation


@pytest.mark.parametrize("mode", ["run", "bash", "script"])
async def test_foreground_and_replay(tmp_path, mode):
    executor = LocalExecutor(tmp_path / "state", "test")
    source = {
        "run": {"argv": [sys.executable, "-c", "print('hello')"]},
        "bash": {"command": "printf hello"},
        "script": {"script": "printf hello"},
    }[mode]
    operation = Operation(
        device_id="test",
        operation_id="exec-one",
        action="bash.execute",
        arguments={"mode": mode, "cwd": str(tmp_path), **source},
    )
    try:
        state, result = await executor(operation)
        data = result["data"]
        assert state == "succeeded", data
        assert data["state"] == "succeeded"
        assert "hello" in data["output"]
        assert "background_pid" not in data
        _, repeated = await executor(operation)
        assert repeated["data"]["execution_id"] == "exec-one"
    finally:
        await executor.aclose()


async def test_background_get_and_stdin(tmp_path):
    executor = LocalExecutor(tmp_path / "state", "test")
    operation = Operation(
        device_id="test",
        operation_id="exec-bg",
        action="bash.execute",
        arguments={
            "mode": "run",
            "cwd": str(tmp_path),
            "timeout": 0.02,
            "argv": [
                sys.executable,
                "-c",
                "import time,sys; time.sleep(.2); print(sys.stdin.read())",
            ],
            "stdin": "payload",
        },
    )
    try:
        _, result = await executor(operation)
        assert result["data"]["code"] == "BACKGROUND"
        assert result["data"]["execution_id"] == "exec-bg"
        for index in range(50):
            query = Operation(
                device_id="test",
                operation_id=f"q-{index}",
                action="bash.get",
                arguments={"execution_id": "exec-bg", "bytes": 1000},
            )
            _, result = await executor(query)
            if result["data"]["state"] != "running":
                break
            await asyncio.sleep(0.02)
        assert result["data"]["state"] == "succeeded", result
        assert "payload" in result["data"]["output"]
    finally:
        await executor.aclose()


async def test_kill_and_script_syntax(tmp_path):
    executor = LocalExecutor(tmp_path / "state", "test")
    try:
        for op_id, args, code in [
            (
                "kill",
                {
                    "mode": "run",
                    "argv": [sys.executable, "-c", "import time;time.sleep(1)"],
                    "timeout": 0.02,
                    "timeout_behavior": "kill",
                },
                "TIMEOUT",
            ),
            ("syntax", {"mode": "script", "script": "if then"}, "SCRIPT_SYNTAX_ERROR"),
        ]:
            _, result = await executor(
                Operation(
                    device_id="test",
                    operation_id=op_id,
                    action="bash.execute",
                    arguments={"cwd": str(tmp_path), **args},
                )
            )
            assert result["data"]["success"] is False
            assert result["data"]["code"] == code, result
    finally:
        await executor.aclose()


async def test_get_does_not_block_foreground_and_restart_is_unknown(tmp_path):
    state_dir = tmp_path / "state"
    executor = LocalExecutor(state_dir, "test")
    op = Operation(
        device_id="test",
        operation_id="long",
        action="bash.execute",
        arguments={
            "mode": "run",
            "cwd": str(tmp_path),
            "timeout": 0.2,
            "argv": [sys.executable, "-c", "import time;time.sleep(2)"],
        },
    )
    executing = asyncio.create_task(executor(op))
    try:
        await asyncio.sleep(0.05)
        query = Operation(
            device_id="test",
            operation_id="query-live",
            action="bash.get",
            arguments={"execution_id": "long", "bytes": 0},
        )
        _, result = await asyncio.wait_for(executor(query), timeout=0.1)
        assert result["data"]["state"] == "running"
        await executing
    finally:
        await executor.aclose()
    recovered = LocalExecutor(state_dir, "test")
    try:
        _, result = await recovered(query)
        assert result["data"]["state"] == "unknown"
        assert result["data"]["exit_code"] is None
    finally:
        await recovered.aclose()


async def test_script_stdin_and_zero_output(tmp_path):
    executor = LocalExecutor(tmp_path / "state", "test")
    try:
        _, result = await executor(
            Operation(
                device_id="test",
                operation_id="script-input",
                action="bash.execute",
                arguments={
                    "mode": "script",
                    "cwd": str(tmp_path),
                    "script": "read -r value; printf '%s' \"$value\"",
                    "stdin": "hello\n",
                    "bytes": 0,
                },
            )
        )
        data = result["data"]
        assert data["exit_code"] == 0
        assert data["output"] == ""
        assert data["returned_output_bytes"] == 0
        assert data["total_bytes"] == 5
    finally:
        await executor.aclose()


def test_new_cli_execution_payloads():
    from device_runtime.operations_cli import (
        OperationsParser,
        add_operations_parser,
        operation_from_args,
    )

    parser = OperationsParser()
    modes = parser.add_subparsers(dest="mode", required=True)
    add_operations_parser(modes)
    args = parser.parse_args(
        [
            "ops",
            "run",
            "--device-id",
            "test",
            "--operation-id",
            "cli-run",
            "--cwd",
            "/tmp",
            "--timeout",
            "0.1",
            "--bytes",
            "0",
            "--",
            "/bin/echo",
            "a b",
        ]
    )
    operation = operation_from_args(args)
    assert operation.action == "bash.execute"
    assert operation.arguments["argv"] == ["/bin/echo", "a b"]
    assert operation.arguments["timeout_behavior"] == "background"
    assert operation.arguments["bytes"] == 0
