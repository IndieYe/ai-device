import asyncio
import base64
import json
import os
import subprocess
import sys

import httpx
import pytest
from conftest import ADMIN, FakeExecutor, running_client

from device_runtime import operations_cli as cli
from device_runtime.http_client import DeviceAPI
from device_runtime.protocol import Operation

CASES = [
    (
        "run",
        ["--cwd", "/tmp", "--", "echo", "hello"],
        "bash.execute",
        {"mode": "run", "argv": ["echo", "hello"], "cwd": "/tmp"},
    ),
    (
        "read-file",
        ["--path", "/tmp/a", "--type", "lines", "--limit", "0"],
        "fs.read_file",
        {"path": "/tmp/a", "type": "lines", "limit": 0},
    ),
    (
        "write-file",
        ["--path", "/tmp/a", "--content", "你好\n"],
        "fs.write_file",
        {"path": "/tmp/a", "content": "你好\n"},
    ),
    (
        "edit-file",
        [
            "--path",
            "/tmp/a",
            "--edits-json",
            '[{"oldText":"a","newText":"b"}]',
            "--dry-run",
            "--include-diff",
        ],
        "fs.edit_file",
        {
            "path": "/tmp/a",
            "edits": [{"oldText": "a", "newText": "b"}],
            "dry_run": True,
            "include_diff": True,
        },
    ),
    (
        "insert-file",
        ["--path", "/tmp/a", "--text", "hello", "--at-end"],
        "fs.insert_file",
        {"path": "/tmp/a", "text": "hello", "at_end": True},
    ),
    ("read-image", ["--path", "/tmp/a.png"], "fs.read_image", {"path": "/tmp/a.png"}),
]


def invoke(argv):
    return cli.main(argv)


def request(command, *args):
    return [command, "--device-id", "laptop-one", "--operation-id", "op-one", *args]


@pytest.fixture
def mock_api(monkeypatch):
    calls = []
    response = {
        "operation_id": "op-one",
        "device_id": "laptop-one",
        "state": "succeeded",
        "result": None,
    }

    def handle(req):
        calls.append(req)
        return httpx.Response(200, json=response)

    original = DeviceAPI.__init__

    def init(self, url, token):
        original(self, url, token, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(DeviceAPI, "__init__", init)
    monkeypatch.setenv("DEVICE_RUNTIME_API_URL", "https://center.test/device-runtime")
    monkeypatch.setenv("DEVICE_RUNTIME_ADMIN_TOKEN", ADMIN)
    return calls, response


@pytest.mark.parametrize("command,options,action,arguments", CASES)
def test_all_action_payloads(mock_api, capsys, command, options, action, arguments):
    calls, _ = mock_api
    assert invoke(request(command, *options)) == 0
    assert len(calls) == 1 and calls[0].method == "POST"
    expected = Operation(
        device_id="laptop-one", operation_id="op-one", action=action, arguments=arguments
    )
    assert json.loads(calls[0].content) == expected.model_dump()
    assert calls[0].url.path == "/device-runtime/v1/operations"
    assert json.loads(capsys.readouterr().out)["code"] == "ok"


def test_directory_and_operation_get(mock_api, capsys):
    calls, response = mock_api
    response.clear()
    # Replace the response body with a valid directory through a new transport.
    calls.clear()
    from unittest.mock import patch

    async def listing(self, **kwargs):
        assert kwargs == {"offset": 2, "limit": 3}
        return {"devices": [], "next_offset": None}

    with patch.object(DeviceAPI, "list_devices", listing):
        assert invoke(["list-devices", "--offset", "2", "--limit", "3"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["devices"] == []
    response.update(operation_id="op-one", device_id="laptop-one", state="unknown", result=None)
    assert invoke(request("get-operation")) == 6
    assert calls[0].method == "GET" and calls[0].url.path.endswith("/op-one")


@pytest.mark.parametrize(
    "state,status,code",
    [
        ("succeeded", 0, "ok"),
        ("failed", 4, "operation_failed"),
        ("dispatching", 5, "operation_pending"),
        ("sent", 5, "operation_pending"),
        ("running", 5, "operation_pending"),
        ("unknown", 6, "operation_unknown"),
    ],
)
def test_receipt_exit_codes(mock_api, capsys, state, status, code):
    _, response = mock_api
    response["state"] = state
    assert invoke(request("get-operation")) == status
    assert json.loads(capsys.readouterr().out)["code"] == code


@pytest.mark.parametrize(
    "options",
    [
        ["run", "--cwd", "relative", "--", "echo"],
        ["run", "--cwd", "/tmp", "--timeout", "0", "--", "echo"],
        ["run", "--cwd", "/tmp", "--timeout", "nan", "--", "echo"],
        ["run", "--cwd", "/tmp", "--timeout", "301", "--", "echo"],
        ["run", "--cwd", "/tmp"],
        ["write-file", "--path", "/tmp/a"],
        ["insert-file", "--path", "/tmp/a", "--text", "x", "--at-end", "--line", "1"],
        ["edit-file", "--path", "/tmp/a", "--edits-json", "private-invalid-json"],
        ["edit-file", "--path", "/tmp/a", "--edits-json", '[{"oldText":"","newText":"x"}]'],
        ["read-image", "--path", "/tmp/a", "--max-bytes", "5242881"],
    ],
)
def test_invalid_inputs_do_not_send_or_echo(mock_api, capsys, options):
    calls, _ = mock_api
    try:
        status = invoke(request(options[0], *options[1:]))
    except SystemExit as exc:
        status = exc.code
    assert status == 2 and not calls
    captured = capsys.readouterr()
    assert "private-invalid-json" not in captured.out + captured.err
    assert json.loads(captured.out)["ok"] is False


def test_local_inputs_and_wait_transport(mock_api, tmp_path, capsys):
    calls, _ = mock_api
    source = tmp_path / "input.txt"
    source.write_text("你好\n")
    assert (
        invoke(
            request(
                "run",
                "--cwd",
                "/tmp",
                "--stdin-file",
                str(source),
                "--timeout",
                "1.5",
                "--",
                "cat",
            )
        )
        == 0
    )
    payload = json.loads(calls[0].content)
    assert payload["arguments"]["stdin"] == "你好\n"
    assert "wait_timeout_ms" not in payload["arguments"]
    assert json.loads(calls[0].content)["arguments"]["timeout"] == 1.5


@pytest.mark.parametrize("failure", ["timeout", "503", "401", "409", "mismatch"])
def test_failures_single_request_reconcile_and_no_secret(monkeypatch, capsys, failure):
    calls = []
    original = DeviceAPI.__init__

    def handle(req):
        calls.append(req)
        if failure == "timeout":
            raise httpx.ReadTimeout(ADMIN, request=req)
        if failure == "mismatch":
            return httpx.Response(200, json={"device_id": "wrong", "operation_id": "op-one"})
        return httpx.Response(int(failure), json={"detail": ADMIN})

    def init(self, url, token):
        original(self, url, token, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(DeviceAPI, "__init__", init)
    monkeypatch.setenv("DEVICE_RUNTIME_API_URL", "https://center.test")
    monkeypatch.setenv("DEVICE_RUNTIME_ADMIN_TOKEN", ADMIN)
    assert invoke(request("write-file", "--path", "/tmp/a", "--content", "secret-payload")) == (
        6 if failure in {"timeout", "503"} else 3
    )
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert ADMIN not in captured.out + captured.err
    assert "secret-payload" not in captured.out + captured.err
    assert json.loads(captured.out)["data"]["operation_id"] == "op-one"


def test_images_raw_or_explicit_exclusive_save(mock_api, capsys, tmp_path):
    calls, response = mock_api
    image = {
        "mime_type": "image/png",
        "base64": base64.b64encode(b"test-image").decode(),
        "size": 10,
        "width": 1,
        "height": 1,
        "animated": False,
    }
    response["result"] = {
        "location": {"namespace": "device", "device_id": "laptop-one"},
        "data": {"success": True, "image": image},
    }
    assert invoke(request("get-operation")) == 0
    assert json.loads(capsys.readouterr().out)["data"]["result"]["data"]["image"]["base64"]
    target = tmp_path / "image.png"
    assert invoke(request("get-operation", "--save-image", str(target))) == 0
    data = json.loads(capsys.readouterr().out)["data"]["result"]["data"]["image"]
    assert "base64" not in data and data["saved_to"] == str(target)
    assert target.read_bytes() == b"test-image"
    count = len(calls)
    assert invoke(request("get-operation", "--save-image", str(target))) == 2
    assert len(calls) == count and target.read_bytes() == b"test-image"


@pytest.mark.parametrize(
    "command", ["ops", *["ops " + c for c in ["list-devices", "get-operation", *cli.ACTIONS]]]
)
def test_real_cli_help(command):
    process = subprocess.run(
        [sys.executable, "-m", "device_runtime.cli", *command.split(), "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0 and "usage:" in process.stdout


async def subprocess_cli(url, *argv):
    env = {key: value for key, value in os.environ.items() if not key.startswith("DEVICE_RUNTIME_")}
    env.update(
        DEVICE_RUNTIME_API_URL=url.replace("ws://", "http://"), DEVICE_RUNTIME_ADMIN_TOKEN=ADMIN
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "device_runtime.cli",
        "ops",
        *argv,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
    assert not stderr, stderr.decode()
    return process.returncode, json.loads(stdout)


async def test_real_cli_submission_dedup_and_get(live_server, tmp_path):
    executor = FakeExecutor()
    async with running_client(live_server, tmp_path / "fake-device", executor):
        url, _, _ = live_server
        status, result = await subprocess_cli(url, "list-devices")
        assert status == 0 and result["data"]["devices"][0]["online"]
        argv = request("run", "--cwd", "/tmp", "--", "echo", "x")
        for _ in range(2):
            status, result = await subprocess_cli(url, *argv)
            assert status == 0 and result["data"]["state"] == "succeeded"
        assert executor.calls == 1
        status, result = await subprocess_cli(url, *request("get-operation"))
        assert status == 0 and result["data"]["device_id"] == "laptop-one"
        changed = [*argv[:-1], "different"]
        status, result = await subprocess_cli(url, *changed)
        assert status == 3 and result["code"] == "center_http_409"
        assert executor.calls == 1


async def test_real_cli_files_commands_tasks_and_image(live_server, tmp_path):
    from PIL import Image

    from device_runtime.executor import LocalExecutor

    executor = LocalExecutor(tmp_path / "real-device", "laptop-one")
    url, _, _ = live_server
    sequence = 0

    async def submit(command, *options):
        nonlocal sequence
        sequence += 1
        operation_id = f"cli-real-{sequence}"
        argv = [command, "--device-id", "laptop-one", "--operation-id", operation_id, *options]
        status, result = await subprocess_cli(url, *argv)
        for _ in range(100):
            if status != 5:
                return status, result
            await asyncio.sleep(0.01)
            status, result = await subprocess_cli(
                url, "get-operation", "--device-id", "laptop-one", "--operation-id", operation_id
            )
        pytest.fail("isolated operation did not complete")

    async with running_client(live_server, tmp_path / "real-device", executor):
        target = tmp_path / "remote.txt"
        status, _ = await submit("write-file", "--path", str(target), "--content", "hello")
        assert status == 0 and target.read_text() == "hello"
        status, result = await submit("read-file", "--path", str(target))
        assert status == 0 and result["data"]["result"]["data"]["content"] == "hello"
        status, _ = await submit(
            "edit-file",
            "--path",
            str(target),
            "--edits-json",
            '[{"oldText":"hello","newText":"world"}]',
        )
        assert status == 0 and target.read_text() == "world"
        status, _ = await submit("insert-file", "--path", str(target), "--text", "!", "--at-end")
        assert status == 0 and target.read_text() == "world!"
        status, result = await submit(
            "run", "--cwd", str(tmp_path), "--", sys.executable, "-c", "raise SystemExit(7)"
        )
        assert status == 4 and result["data"]["result"]["data"]["exit_code"] == 7
        status, result = await submit(
            "run", "--cwd", str(tmp_path), "--", sys.executable, "-c", "print('task-output')"
        )
        assert status == 0
        execution_id = result["data"]["operation_id"]
        status, result = await submit(
            "get", "--execution-id", execution_id, "--timeout", "3", "--bytes", "1000"
        )
        assert status == 0
        assert "task-output" in result["data"]["result"]["data"]["output"]
        image = tmp_path / "remote.png"
        Image.new("RGB", (2, 3)).save(image)
        status, result = await submit("read-image", "--path", str(image))
        assert status == 0
        saved = tmp_path / "download.png"
        status, result = await subprocess_cli(
            url,
            "get-operation",
            "--device-id",
            "laptop-one",
            "--operation-id",
            result["data"]["operation_id"],
            "--save-image",
            str(saved),
        )
        assert status == 0 and saved.read_bytes() == image.read_bytes()


def test_file_and_stdin_inputs(mock_api, tmp_path, monkeypatch, capsys):
    import io

    calls, _ = mock_api
    monkeypatch.setattr(sys, "stdin", io.StringIO("from-stdin"))
    assert invoke(request("write-file", "--path", "/remote", "--content-file", "-")) == 0
    assert json.loads(calls[-1].content)["arguments"]["content"] == "from-stdin"
    edits = tmp_path / "edits.json"
    edits.write_text('[{"oldText":"x","newText":"y"}]')
    assert invoke(request("edit-file", "--path", "/remote", "--edits-file", str(edits))) == 0
    assert json.loads(calls[-1].content)["arguments"]["edits"][0]["newText"] == "y"
    count = len(calls)
    assert (
        invoke(request("write-file", "--path", "/remote", "--content-file", str(edits / "missing")))
        == 3
    )
    assert len(calls) == count


def test_image_invalid_bytes_and_pending_do_not_create_file(mock_api, tmp_path, capsys):
    _, response = mock_api
    target = tmp_path / "image.png"
    response["state"] = "sent"
    assert invoke(request("get-operation", "--save-image", str(target))) == 5
    assert not target.exists()
    response["state"] = "succeeded"
    response["result"] = {
        "location": {"namespace": "device", "device_id": "laptop-one"},
        "data": {
            "image": {
                "mime_type": "image/png",
                "base64": "eA==",
                "size": 2,
                "width": 1,
                "height": 1,
                "animated": False,
            }
        },
    }
    assert invoke(request("get-operation", "--save-image", str(target))) == 2
    assert not target.exists()


async def test_cli_command_set_matches_mcp(monkeypatch):
    from device_runtime import mcp_server

    monkeypatch.setattr(mcp_server, "DeviceAPI", lambda *args: object())
    mcp = mcp_server.create_mcp("http://127.0.0.1:1", ADMIN)
    names = {tool.name for tool in await mcp.list_tools()}
    expected = {
        command.replace("-", "_") for command in ["list-devices", "get-operation", *cli.ACTIONS]
    }
    assert names == expected | {"get_version"}


def test_local_input_bounded_before_submit(mock_api, tmp_path, capsys):
    calls, _ = mock_api
    source = tmp_path / "large.txt"
    source.write_text("x" * (cli.MAX_OPERATION_BYTES + 1))
    assert invoke(request("write-file", "--path", "/remote", "--content-file", str(source))) == 2
    assert not calls
    assert json.loads(capsys.readouterr().out)["code"] == "input_too_large"


def test_safe_configuration_and_interruption(mock_api, monkeypatch, capsys):
    calls, _ = mock_api
    monkeypatch.setenv("DEVICE_RUNTIME_API_URL", "https://user:private-sentinel@center.test")
    assert invoke(request("get-operation")) == 2
    assert not calls and "private-sentinel" not in capsys.readouterr().out

    async def interrupted(args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "execute", interrupted)
    assert invoke(request("get-operation")) == 130
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["operation_id"] == "op-one"
    assert "does not cancel" in payload["data"]["recovery"]


def test_no_mcp_or_executor_imports_in_operations():
    script = """
import sys
from device_runtime.operations_cli import main
try:
    main(['--help'])
except SystemExit as exc:
    assert exc.code == 0
assert not any(name == 'mcp' or name.startswith('mcp.') for name in sys.modules)
assert 'device_runtime.executor' not in sys.modules
assert not any(name.startswith(('mcp_server_bash', 'mcp_server_fs')) for name in sys.modules)
"""
    process = subprocess.run(
        [sys.executable, "-c", script], check=False, capture_output=True, text=True, timeout=10
    )
    assert process.returncode == 0, process.stderr


@pytest.mark.parametrize(
    "image_data",
    [
        {"image": {"base64": "private-malformed-image"}},
        {
            "image": {
                "mime_type": "image/png",
                "base64": "!!!",
                "size": 1,
                "width": 1,
                "height": 1,
                "animated": False,
            }
        },
        ["invalid-result-data"],
    ],
)
def test_malformed_image_keeps_recovery_identity(mock_api, tmp_path, capsys, image_data):
    calls, response = mock_api
    response["result"] = {
        "location": {"namespace": "device", "device_id": "laptop-one"},
        "data": image_data,
    }
    target = tmp_path / "image.png"
    assert invoke(request("get-operation", "--save-image", str(target))) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "invalid_image_payload"
    assert payload["data"]["device_id"] == "laptop-one"
    assert payload["data"]["operation_id"] == "op-one"
    assert "private-malformed-image" not in json.dumps(payload)
    assert len(calls) == 1 and not target.exists()
