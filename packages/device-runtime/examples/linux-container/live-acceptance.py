"""One-shot real MCP acceptance against the newly authorized instance only."""

import asyncio
import base64
import hashlib
import json
import os
import struct
import uuid
import zlib
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path("/work/instance")
OPERATOR = Path("/work/operator")
EVIDENCE = Path("/work/evidence")
SCRATCH = EVIDENCE / "scratch"
DEVICE = "ubuntu-arm64-test"
PREFIX = "linux-" + uuid.uuid4().hex[:16]
MANIFEST = EVIDENCE / "operation-manifest.json"
assert not MANIFEST.exists(), "Existing acceptance operations: inspect, never rerun the script."
report = {
    "status": "running",
    "device_id": DEVICE,
    "prefix": PREFIX,
    "scratch": str(SCRATCH),
    "operations": [],
    "checks": {},
    "entry": "explicit environment credentials -> test harness stdio MCP -> container loopback HTTP -> WS -> installed root/run client Bash/FS",
}


def save():
    MANIFEST.write_text(json.dumps(report, ensure_ascii=False, indent=2))


save()


def chunk(kind, data):
    return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))


png = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    + chunk(b"IEND", b"")
)
active = json.loads((ROOT / "active.json").read_text())
client_python = str(ROOT / "releases" / active["release"] / "client/venv/bin/python")


def receipt_of(result):
    assert not result.isError, "MCP protocol error; retain evidence and inspect before retry"
    value = result.structuredContent
    if value is None:
        value = json.loads(
            next(content.text for content in result.content if content.type == "text")
        )
    return value


async def main():
    params = StdioServerParameters(
        command=str(OPERATOR / "launch.py"),
        args=["mcp"],
        env={k: os.environ[k] for k in ("DEVICE_RUNTIME_API_URL", "DEVICE_RUNTIME_ADMIN_TOKEN")},
    )
    with (EVIDENCE / "live-acceptance.stderr.log").open("w") as log:
        async with (
            stdio_client(params, errlog=log) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 11
            report["checks"]["tools"] = 11
            devices = receipt_of(await session.call_tool("list_devices", {}))
            assert len(devices["devices"]) == 1
            assert devices["devices"][0]["device_id"] == DEVICE and devices["devices"][0]["online"]
            report["checks"]["explicit_new_device_online"] = True

            async def submit(tool, suffix, arguments):
                operation = PREFIX + "-" + suffix
                args = {"device_id": DEVICE, "operation_id": operation, **arguments}
                item = {
                    "tool": tool,
                    "operation_id": operation,
                    "arguments": arguments,
                    "state": "not_submitted",
                }
                report["operations"].append(item)
                save()
                initial = await session.call_tool(tool, args)
                receipt = receipt_of(initial)
                item["initial_state"] = receipt["state"]
                save()
                result = initial
                for _ in range(200):
                    if receipt["state"] in ("succeeded", "failed", "unknown"):
                        item["state"] = receipt["state"]
                        item["receipt"] = receipt
                        save()
                        assert receipt["state"] == "succeeded", (
                            "Non-successful or uncertain operation; do not replace IDs or replay."
                        )
                        assert receipt["device_id"] == DEVICE
                        return receipt, result
                    await asyncio.sleep(0.05)
                    result = await session.call_tool(
                        "get_operation", {"device_id": DEVICE, "operation_id": operation}
                    )
                    receipt = receipt_of(result)
                raise AssertionError(
                    "Operation receipt timeout: inspect existing operation_id, do not rerun."
                )

            source = "from pathlib import Path;import base64,sys;p=Path(sys.argv[1]);p.mkdir(mode=0o700);(p/'counter.txt').write_text('1');(p/'sample.png').write_bytes(base64.b64decode(sys.argv[2]));print(sys.stdin.read(),end='')"
            argv_args = {
                "argv": [
                    client_python,
                    "-I",
                    "-c",
                    source,
                    str(SCRATCH),
                    base64.b64encode(png).decode(),
                ],
                "cwd": str(EVIDENCE),
                "stdin": "中文命令与标准输入验证\n",
                "output_bytes": 2048,
            }
            receipt, _ = await submit("run", "argv", argv_args)
            assert "中文命令与标准输入验证" in json.dumps(receipt, ensure_ascii=False)
            duplicate = receipt_of(
                await session.call_tool(
                    "run",
                    {"device_id": DEVICE, "operation_id": PREFIX + "-argv", **argv_args},
                )
            )
            assert duplicate["state"] == "succeeded" and duplicate["result"] == receipt["result"]
            report["checks"]["same_id_same_request_returns_existing_receipt"] = True
            await submit(
                "read_file",
                "counter",
                {"path": str(SCRATCH / "counter.txt"), "max_chars": 16},
            )
            text = SCRATCH / "sample.txt"
            await submit("write_file", "write", {"path": str(text), "content": "第一行\n第二行\n"})
            await submit(
                "edit_file",
                "edit-dry",
                {
                    "path": str(text),
                    "edits": [{"oldText": "第一行", "newText": "预演不能落盘"}],
                    "dry_run": True,
                    "include_diff": True,
                },
            )
            receipt, _ = await submit(
                "read_file", "read-after-edit-dry", {"path": str(text), "max_chars": 256}
            )
            serial = json.dumps(receipt, ensure_ascii=False)
            assert "第一行" in serial and "预演不能落盘" not in serial
            await submit(
                "edit_file",
                "edit",
                {"path": str(text), "edits": [{"oldText": "第一行", "newText": "已编辑"}]},
            )
            await submit(
                "insert_file",
                "insert-dry",
                {"path": str(text), "text": "预演不能落盘\n", "at_end": True, "dry_run": True},
            )
            receipt, _ = await submit(
                "read_file",
                "read-after-insert-dry",
                {"path": str(text), "max_chars": 256},
            )
            serial = json.dumps(receipt, ensure_ascii=False)
            assert "已编辑" in serial and "预演不能落盘" not in serial
            await submit(
                "insert_file",
                "insert",
                {"path": str(text), "text": "追加行\n", "at_end": True},
            )
            receipt, _ = await submit(
                "read_file",
                "read-lines",
                {"path": str(text), "type": "lines", "offset": 0, "limit": 3, "max_chars": 256},
            )
            serial = json.dumps(receipt, ensure_ascii=False)
            assert all(value in serial for value in ("已编辑", "第二行", "追加行"))
            report["checks"]["file_write_edit_insert_line_read_and_two_dry_runs"] = True
            receipt, image_result = await submit(
                "read_image",
                "image",
                {"path": str(SCRATCH / "sample.png"), "max_bytes": 1024},
            )
            image = next(content for content in image_result.content if content.type == "image")
            assert image.mimeType == "image/png" and base64.b64decode(image.data) == png
            report["checks"]["standard_mcp_png_bytes_match"] = hashlib.sha256(png).hexdigest()
            expected = "后台输出：你好\n".encode()
            task_id = PREFIX + "-task"
            await submit(
                "run",
                "task",
                {
                    "argv": [
                        client_python,
                        "-I",
                        "-u",
                        "-c",
                        "import sys,time;time.sleep(.2);sys.stdout.write('后台输出：你好\\n')",
                    ],
                    "cwd": str(SCRATCH),
                    "timeout": 0.01,
                },
            )
            response = await session.call_tool(
                "get",
                {
                    "device_id": DEVICE,
                    "execution_id": task_id,
                    "timeout": 5,
                    "bytes": 1024,
                },
            )
            receipt = json.loads(response.content[0].text)
            snapshot = receipt["result"]["data"]
            assert snapshot["state"] == "succeeded" and snapshot["exit_code"] == 0
            output = snapshot["output"].encode()
            assert output == expected
            report["checks"]["task_terminal_and_7byte_utf8_cursor_lossless"] = True
            report["checks"]["task_output_sha256"] = hashlib.sha256(expected).hexdigest()
            report["status"] = "PASS"
            report["accepted_operation_count"] = len(report["operations"])
            report["boundaries"] = (
                "Isolated Ubuntu container only; temporary loopback center and independent stdio MCP; NOT Mac main ServerPool or public network; lifecycle handled separately after temporary processes exit."
            )
            save()
            (EVIDENCE / "live-acceptance-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            print(
                json.dumps(
                    {
                        "status": report["status"],
                        "device_id": DEVICE,
                        "tools": 11,
                        "operations": len(report["operations"]),
                        "checks": report["checks"],
                    },
                    ensure_ascii=False,
                )
            )


try:
    asyncio.run(main())
except BaseException as exc:
    report["status"] = "FAILED_NEEDS_RECONCILIATION"
    report["failure_type"] = type(exc).__name__
    save()
    raise
