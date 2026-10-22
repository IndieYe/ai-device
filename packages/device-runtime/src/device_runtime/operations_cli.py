"""Center operation CLI; never imports device executors or retries submissions."""

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from .protocol import MAX_IMAGE_BYTES, MAX_OPERATION_BYTES, ImagePayload, Operation


class InputError(ValueError):
    pass


def emit(ok, code, *, data=None):
    print(json.dumps({"ok": ok, "code": code, "data": data}, ensure_ascii=False))


class OperationsParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse messages may echo command arguments, including file contents.
        emit(False, "invalid_arguments")
        self.exit(2)


ACTIONS = {
    "bash": "bash.execute",
    "run": "bash.execute",
    "script": "bash.execute",
    "get": "bash.get",
    "read-file": "fs.read_file",
    "write-file": "fs.write_file",
    "edit-file": "fs.edit_file",
    "insert-file": "fs.insert_file",
    "read-image": "fs.read_image",
}


def add_operations_parser(modes):
    parser = modes.add_parser("ops", help="Operate explicit devices through the center API")
    parser.add_argument("--server-url", help="Default: DEVICE_RUNTIME_API_URL")
    parser.add_argument("--token-env", default="DEVICE_RUNTIME_ADMIN_TOKEN")
    commands = parser.add_subparsers(
        dest="operation_command", required=True, parser_class=OperationsParser
    )
    commands.add_parser("version", help="Query the running center version (read-only)")
    listing = commands.add_parser("list-devices", help="List one bounded page of devices")
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--limit", type=int, default=40)
    query = commands.add_parser("get-operation", help="Reconcile an existing operation ID")
    query.add_argument("--device-id", required=True)
    query.add_argument("--operation-id", required=True)
    query.add_argument("--save-image", type=Path, help="Local destination; never overwrite")
    for command in ACTIONS:
        item = commands.add_parser(
            command,
            description=(
                "Requires a stable operation ID. No automatic retries or local fallback. "
                "A submission receipt is not completion. Query the same ID after uncertainty. "
                "Unified get uses execution-id; low-level task snapshots require fresh query IDs."
            ),
        )
        item.add_argument("--device-id", required=True)
        item.add_argument("--operation-id", required=command != "get")
        if command in {"bash", "run", "script"}:
            item.add_argument("--cwd", required=True)
            item.add_argument("--timeout", type=float, default=60)
            item.add_argument(
                "--timeout-behavior", choices=["background", "kill"], default="background"
            )
            item.add_argument("--bytes", type=int, default=8192)
            item.add_argument("--purpose")
            stdin = item.add_mutually_exclusive_group()
            stdin.add_argument("--stdin")
            stdin.add_argument("--stdin-file")
            if command == "run":
                item.add_argument("argv", nargs=argparse.REMAINDER)
            elif command == "bash":
                item.add_argument("--command", required=True)
            else:
                source = item.add_mutually_exclusive_group(required=True)
                source.add_argument("--script")
                source.add_argument("--script-file")
        elif command == "get":
            item.add_argument("--execution-id", required=True)
            item.add_argument("--timeout", type=float, default=0)
            item.add_argument("--bytes", type=int, default=200)
        else:
            item.add_argument("--path", required=True, help="Absolute device path, not local path")
            if command == "read-file":
                item.add_argument("--type", choices=("chars", "lines"), default="chars")
                item.add_argument("--offset", type=int, default=0)
                item.add_argument("--limit", type=int)
                item.add_argument("--direction", choices=("forward", "backward"), default="forward")
                item.add_argument("--max-chars", type=int, default=10000)
            elif command in {"write-file", "insert-file"}:
                field = "content" if command == "write-file" else "text"
                source = item.add_mutually_exclusive_group(required=True)
                source.add_argument("--" + field)
                source.add_argument(
                    "--" + field + "-file", help="Local UTF-8 source, or - for stdin"
                )
                if command == "insert-file":
                    position = item.add_mutually_exclusive_group(required=True)
                    position.add_argument("--line", type=int)
                    position.add_argument("--before")
                    position.add_argument("--after")
                    position.add_argument("--at-end", action="store_true")
            elif command == "edit-file":
                edits = item.add_mutually_exclusive_group(required=True)
                edits.add_argument(
                    "--edits-json", help='JSON array of {"oldText":...,"newText":...}'
                )
                edits.add_argument("--edits-file", help="Local UTF-8 JSON source, or - for stdin")
            elif command == "read-image":
                item.add_argument("--max-bytes", type=int, default=MAX_IMAGE_BYTES)
                item.add_argument(
                    "--save-image", type=Path, help="Local destination; never overwrite"
                )
            if command in {"edit-file", "insert-file"}:
                item.add_argument("--dry-run", action="store_true")
                item.add_argument("--include-diff", action="store_true")
    return parser


def read_local_text(path):
    # Local input is always explicit. Device paths are never opened locally.
    if path == "-":
        text = sys.stdin.read(MAX_OPERATION_BYTES + 1)
    else:
        with Path(path).open(encoding="utf-8") as stream:
            text = stream.read(MAX_OPERATION_BYTES + 1)
    if len(text.encode("utf-8")) > MAX_OPERATION_BYTES:
        raise InputError("input_too_large")
    return text


def operation_from_args(args):
    values = vars(args).copy()
    command = args.operation_command
    for field in ("stdin", "content", "text"):
        path = values.pop(field + "_file", None)
        if path is not None:
            values[field] = read_local_text(path)
    if command == "edit-file":
        text = read_local_text(args.edits_file) if args.edits_file else args.edits_json
        values["edits"] = json.loads(text)
    if command == "run":
        argv = values["argv"]
        values["argv"] = argv[1:] if argv[:1] == ["--"] else argv
    if command in {"bash", "run", "script"}:
        values["mode"] = command
        if command == "bash":
            values["command"] = args.command
        if command == "script" and args.script_file:
            values["script"] = read_local_text(args.script_file)
    from .protocol import ARGUMENTS

    model = ARGUMENTS[ACTIONS[command]]
    arguments = {key: values[key] for key in model.model_fields if key in values}
    return Operation(
        device_id=args.device_id,
        operation_id=args.operation_id,
        action=ACTIONS[command],
        arguments=arguments,
    )


def save_image(receipt, destination):
    result = receipt.get("result") or {}
    data = result.get("data") or {}
    if not isinstance(data, dict):
        raise InputError("invalid_image_payload")
    image = data.get("image")
    if image is None:
        if receipt.get("state") == "succeeded":
            raise InputError("image_unavailable")
        return receipt
    try:
        payload = ImagePayload.model_validate(image)
        raw = base64.b64decode(payload.base64, validate=True)
    except (ValidationError, ValueError):
        raise InputError("invalid_image_payload") from None
    if len(raw) != payload.size:
        raise InputError("invalid_image_payload")
    # Exclusive create: an existing file is never silently overwritten.
    with destination.open("xb") as stream:
        stream.write(raw)
    result = receipt["result"]
    return {
        **receipt,
        "result": {
            **result,
            "data": {
                **result["data"],
                "image": {**payload.model_dump(exclude={"base64"}), "saved_to": str(destination)},
            },
        },
    }


async def execute(args):
    from .http_client import DeviceAPI

    # Validate payload before network access (including reading explicit local inputs).
    operation = (
        operation_from_args(args)
        if args.operation_command in ACTIONS and args.operation_command != "get"
        else None
    )
    if getattr(args, "save_image", None) is not None and args.save_image.exists():
        raise InputError("local_destination_exists")
    async with DeviceAPI(
        args.server_url or os.environ.get("DEVICE_RUNTIME_API_URL", ""),
        os.environ.get(args.token_env, ""),
    ) as api:
        if args.operation_command == "version":
            return await api.get_version()
        if args.operation_command == "list-devices":
            return await api.list_devices(offset=args.offset, limit=args.limit)
        if args.operation_command == "get":
            return await api.get_execution(
                args.device_id, args.execution_id, timeout=args.timeout, output_bytes=args.bytes
            )
        if operation is not None and operation.action == "bash.execute":
            return await api.execute(args.device_id, args.operation_id, operation.arguments)
        if operation is None:
            receipt = await api.get_operation(args.device_id, args.operation_id)
        else:
            receipt = await api.submit(operation)
    if getattr(args, "save_image", None) is not None:
        receipt = save_image(receipt, args.save_image)
    return receipt


def recovery_context(args):
    return {
        "device_id": getattr(args, "device_id", None),
        "operation_id": getattr(args, "operation_id", None),
        "recovery": "Query the same ID; never change ID or blindly replay.",
    }


def run_operations(args):
    try:
        data = asyncio.run(execute(args))
    except ImportError:
        emit(False, "operations_dependency_unavailable")
        return 2
    except InputError as exc:
        emit(False, str(exc), data=recovery_context(args))
        return 2
    except (ValidationError, ValueError, UnicodeError):
        # Never render Pydantic/JSON errors: they may include sensitive input values.
        emit(False, "invalid_input_or_configuration", data=recovery_context(args))
        return 2
    except OSError:
        emit(False, "local_io_error", data=recovery_context(args))
        return 3
    except KeyboardInterrupt:
        emit(
            False,
            "interrupted",
            data={
                "operation_id": getattr(args, "operation_id", None),
                "recovery": "Query the same ID; interruption does not cancel remote execution.",
            },
        )
        return 130
    except RuntimeError as exc:
        from .http_client import DeviceAPIError

        if not isinstance(exc, DeviceAPIError):
            raise
        emit(
            False,
            exc.code,
            data={
                "device_id": getattr(args, "device_id", None),
                "operation_id": exc.operation_id or getattr(args, "operation_id", None),
                "execution_id": exc.execution_id or getattr(args, "execution_id", None),
                "recovery": "Query the same ID; never change ID or blindly replay.",
            },
        )
        return 6 if exc.code == "operation_result_uncertain" else 3
    state = data.get("state")
    code, status = {
        "failed": ("operation_failed", 4),
        "unknown": ("operation_unknown", 6),
        "dispatching": ("operation_pending", 5),
        "sent": ("operation_pending", 5),
        "running": ("operation_pending", 5),
    }.get(state, ("ok", 0))
    emit(status == 0, code, data=data)
    return status


def main(argv):
    parser = OperationsParser(prog="device-runtime")
    modes = parser.add_subparsers(dest="mode", required=True, parser_class=OperationsParser)
    add_operations_parser(modes)
    return run_operations(parser.parse_args(["ops", *argv]))
