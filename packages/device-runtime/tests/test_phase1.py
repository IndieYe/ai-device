"""Phase-one contracts and real stdio/HTTP/WebSocket/paired-core execution."""

import asyncio
import hashlib
import json
import sys
import threading
from pathlib import Path

import pytest
from conftest import running_client
from pydantic import ValidationError

from device_runtime.executor import LocalExecutor
from device_runtime.protocol import Operation, canonical


def operation(action, arguments, operation_id="phase1-one"):
    return Operation(
        device_id="laptop-one", operation_id=operation_id, action=action, arguments=arguments
    )


@pytest.mark.parametrize(
    "action,args,extensions",
    [
        (
            "fs.read_file",
            {"path": "/tmp/f", "max_chars": 10000},
            {"type": "chars", "offset": 0, "limit": None, "direction": "forward"},
        ),
        (
            "fs.edit_file",
            {"path": "/tmp/f", "edits": [{"oldText": "a", "newText": "b"}]},
            {"dry_run": False, "include_diff": False},
        ),
    ],
)
def test_optional_defaults_preserve_existing_journal_digest(action, args, extensions):
    original = {
        "device_id": "laptop-one",
        "operation_id": "phase1-one",
        "action": action,
        "arguments": args,
    }
    old_digest = hashlib.sha256(canonical(original).encode()).hexdigest()
    assert operation(action, args).digest == old_digest
    assert operation(action, {**args, **extensions}).digest == old_digest


@pytest.mark.parametrize(
    "action,args",
    [
        ("bash.execute", {"mode": "run", "argv": ["echo"], "cwd": "/tmp", "stdin": 1}),
        ("bash.execute", {"mode": "run", "argv": ["echo"], "cwd": "/tmp", "stdin": "x" * 262145}),
        ("bash.execute", {"mode": "run", "argv": ["echo"], "cwd": "/tmp", "stdin": "中" * 262144}),
        ("fs.read_file", {"path": "/tmp/f", "offset": -1}),
        ("fs.read_file", {"path": "/tmp/f", "offset": True}),
        ("fs.read_file", {"path": "/tmp/f", "limit": "2"}),
        ("fs.read_file", {"path": "/tmp/f", "direction": "sideways"}),
        (
            "fs.edit_file",
            {"path": "/tmp/f", "edits": [{"oldText": "a", "newText": "b"}], "dry_run": "true"},
        ),
        ("fs.insert_file", {"path": "/tmp/f", "text": "x"}),
        ("fs.insert_file", {"path": "/tmp/f", "text": "x", "line": 1, "at_end": True}),
        ("fs.insert_file", {"path": "/tmp/f", "text": "x", "line": True}),
        ("fs.insert_file", {"path": "/tmp/f", "text": "x", "before": ""}),
        ("fs.insert_file", {"path": "/tmp/f", "text": "x", "at_end": 1}),
    ],
)
def test_invalid_extension_arguments(action, args):
    with pytest.raises(ValidationError):
        operation(action, args)


@pytest.mark.parametrize("stdin", ['中文\nJSON {"a": 1}\n', ""])
async def test_stdin_roundtrip_and_artifact_cleanup(tmp_path, stdin):
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    try:
        state, result = await executor(
            operation(
                "bash.execute",
                {
                    "mode": "run",
                    "argv": [
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.write(sys.stdin.read())",
                    ],
                    "cwd": str(tmp_path),
                    "stdin": stdin,
                },
            )
        )
        assert state == "succeeded"
        assert result["data"]["output"] == stdin
        assert not list((tmp_path / "runtime/artifacts").glob("*/stdin.bin"))
    finally:
        await executor.aclose()


async def test_cancelled_stdin_preparation_releases_owned_artifact(tmp_path):
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    started, release = threading.Event(), threading.Event()
    original = executor._create_stdin

    def slow_prepare(*args):
        started.set()
        release.wait(5)
        return original(*args)

    executor._create_stdin = slow_prepare
    task = asyncio.create_task(
        executor(
            operation(
                "bash.execute",
                {
                    "mode": "run",
                    "argv": [sys.executable, "-c", "raise SystemExit('must not run')"],
                    "cwd": str(tmp_path),
                    "stdin": "isolated input",
                },
            )
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await executor.aclose()
    assert not list((tmp_path / "runtime/artifacts").glob("*/stdin.bin"))


async def test_slow_stdin_preparation_keeps_device_api_responsive(live_server, tmp_path):
    _, _, http = live_server
    state = tmp_path / "client"
    executor = LocalExecutor(state, "laptop-one")
    started, release = threading.Event(), threading.Event()
    original = executor._create_stdin

    def slow_prepare(*args):
        started.set()
        release.wait(5)
        return original(*args)

    executor._create_stdin = slow_prepare
    async with running_client(live_server, state, executor):
        try:
            response = await http.post(
                "/v1/operations",
                json=operation(
                    "bash.execute",
                    {
                        "mode": "run",
                        "argv": [
                            sys.executable,
                            "-c",
                            "import sys; sys.stdout.write(sys.stdin.read())",
                        ],
                        "cwd": str(tmp_path),
                        "stdin": "slow input",
                    },
                ).model_dump(),
            )
            assert response.status_code == 200
            assert await asyncio.to_thread(started.wait, 5)
            response = await asyncio.wait_for(http.get("/v1/devices"), 0.5)
            assert response.status_code == 200
        finally:
            release.set()


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"type": "lines", "offset": 1, "limit": 1}, "二\n"),
        ({"type": "lines", "direction": "backward", "limit": 1}, "三\n"),
        ({"type": "chars", "offset": 2, "limit": 2}, "二\n"),
        ({"direction": "backward", "offset": 2, "limit": 2}, "二\n"),
        ({"type": "lines", "direction": "backward", "offset": 4, "limit": 1}, ""),
        ({"direction": "backward", "offset": 7, "limit": 1}, ""),
        ({"type": "lines", "limit": 0}, ""),
        ({"limit": 0}, ""),
    ],
)
async def test_file_ranges(tmp_path, args, expected):
    file = tmp_path / "中文 空格.txt"
    file.write_text("一\n二\n三\n")
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    try:
        state, result = await executor(operation("fs.read_file", {"path": str(file), **args}))
        assert state == "succeeded"
        assert result["data"]["content"] == expected
        if args.get("type") == "lines" and expected:
            expected_line = 3 if args.get("direction") == "backward" else 2
            assert result["data"]["line_range"] == f"{expected_line}-{expected_line}"
    finally:
        await executor.aclose()


@pytest.mark.parametrize(
    "position,expected",
    [
        ({"line": 2}, "a\nXb\n"),
        ({"before": "b"}, "a\nXb\n"),
        ({"after": "a"}, "aX\nb\n"),
        ({"at_end": True}, "a\nb\nX"),
    ],
)
async def test_insert_positions_and_preview(tmp_path, position, expected):
    file = tmp_path / "insert.txt"
    file.write_text("a\nb\n")
    executor = LocalExecutor(tmp_path / "runtime", "laptop-one")
    try:
        args = {"path": str(file), "text": "X", **position, "include_diff": True}
        state, preview = await executor(operation("fs.insert_file", {**args, "dry_run": True}))
        assert state == "succeeded" and preview["data"]["dry_run"]
        assert file.read_text() == "a\nb\n"
        assert "X" in preview["data"]["diff"]["content"]
        state, _ = await executor(operation("fs.insert_file", args, "phase1-insert-real"))
        assert state == "succeeded" and file.read_text() == expected
    finally:
        await executor.aclose()


async def test_real_stdio_extensions_and_deduplication(live_server, tmp_path):
    mcp = pytest.importorskip("mcp")
    stdio_client = pytest.importorskip("mcp.client.stdio").stdio_client
    url, service, http = live_server
    state_dir = tmp_path / "client"
    executor = LocalExecutor(state_dir, "laptop-one")
    params = mcp.StdioServerParameters(
        command=sys.executable,
        args=["-m", "device_runtime.mcp_server"],
        env={
            "DEVICE_RUNTIME_API_URL": url.replace("ws://", "http://"),
            "DEVICE_RUNTIME_ADMIN_TOKEN": "test-only-admin-token-not-a-secret",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    async with (
        running_client(live_server, state_dir, executor),
        stdio_client(params) as (read, write),
        mcp.ClientSession(read, write) as session,
    ):
        await session.initialize()
        names = {t.name for t in (await session.list_tools()).tools}
        assert "insert_file" in names
        assert "device_apply_patch" not in names

        async def call(tool, op_id, args):
            request = {"device_id": "laptop-one", "operation_id": op_id, **args}
            response = await session.call_tool(tool, request)
            assert not response.isError, response.content
            async with asyncio.timeout(5):
                while True:
                    response = await session.call_tool(
                        "get_operation",
                        {
                            "device_id": "laptop-one",
                            "operation_id": op_id,
                        },
                    )
                    assert not response.isError, response.content
                    receipt = response.structuredContent or json.loads(
                        "".join(block.text for block in response.content if block.type == "text")
                    )
                    if receipt["state"] in {"succeeded", "failed", "unknown"}:
                        assert receipt["result"]["location"] == {
                            "namespace": "device",
                            "device_id": "laptop-one",
                        }
                        return receipt
                    await asyncio.sleep(0.01)

        file = tmp_path / "stdio 中文.txt"
        await call("write_file", "p1-write", {"path": str(file), "content": "a\nb\n"})
        got = await call(
            "read_file",
            "p1-read",
            {"path": str(file), "type": "lines", "offset": 1, "limit": 1},
        )
        assert got["result"]["data"]["content"] == "b\n"
        got = await call(
            "run",
            "p1-stdin",
            {
                "mode": "run",
                "argv": [sys.executable, "-c", "import sys; print(sys.stdin.read(),end='')"],
                "cwd": str(tmp_path),
                "stdin": "中文输入\n",
            },
        )
        assert got["result"]["data"]["output"] == "中文输入\n"
        got = await call(
            "edit_file",
            "p1-preview",
            {
                "path": str(file),
                "edits": [{"oldText": "a", "newText": "A"}],
                "dry_run": True,
                "include_diff": True,
            },
        )
        assert got["result"]["data"]["dry_run"] and file.read_text() == "a\nb\n"
        insert = {"path": str(file), "text": "X", "at_end": True}
        await call("insert_file", "p1-once", insert)
        await call("insert_file", "p1-once", insert)
        assert file.read_text() == "a\nb\nX"
        conflict = await session.call_tool(
            "insert_file",
            {"device_id": "laptop-one", "operation_id": "p1-once", **insert, "text": "Y"},
        )
        assert conflict.isError and file.read_text() == "a\nb\nX"
        bad = await session.call_tool(
            "insert_file",
            {"device_id": "laptop-one", "operation_id": "p1-invalid", **insert, "line": 1},
        )
        assert bad.isError
        assert (await http.get("/v1/operations/p1-invalid")).status_code == 404
        for index, (tool, args) in enumerate(
            [
                ("read_file", {"path": str(file), "offset": True}),
                ("read_file", {"path": str(file), "limit": "1"}),
                (
                    "edit_file",
                    {
                        "path": str(file),
                        "edits": [{"oldText": "a", "newText": "b"}],
                        "dry_run": "true",
                    },
                ),
                ("insert_file", {**insert, "at_end": 1}),
            ]
        ):
            op_id = f"p1-invalid-type-{index}"
            bad = await session.call_tool(
                tool, {"device_id": "laptop-one", "operation_id": op_id, **args}
            )
            assert bad.isError
            assert (await http.get(f"/v1/operations/{op_id}")).status_code == 404
        huge_file = tmp_path / "large-diff.txt"
        original = "a" + "x" * 1_048_576
        huge_file.write_text(original)
        got = await call(
            "edit_file",
            "p1-large-preview",
            {
                "path": str(huge_file),
                "edits": [{"oldText": "a", "newText": "b"}],
                "dry_run": True,
                "include_diff": True,
            },
        )
        assert got["state"] == "succeeded"
        diff = got["result"]["data"]["diff"]
        assert diff["truncated"] is True and len(diff["content"]) <= 10000
        assert diff["total_chars"] > 1_048_576
        assert Path(diff["temp_file"]).is_relative_to(state_dir / "file-results")
        diff_chunk = await call(
            "read_file",
            "p1-diff-chunk",
            {
                "path": diff["temp_file"],
                "limit": 64,
            },
        )
        assert diff_chunk["result"]["data"]["content"].startswith("--- ")
        assert huge_file.read_text() == original
        assert "laptop-one" in service.peers
