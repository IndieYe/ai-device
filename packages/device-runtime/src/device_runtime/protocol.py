"""Version-one explicit device and operation contracts."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VERSION = 1
MAX_MESSAGE_BYTES = 8 * 1_048_576
MAX_OPERATION_BYTES = 524288
MAX_IMAGE_BYTES = 5 * 1_048_576
Action = Literal[
    "bash.execute",
    "bash.get",
    "fs.read_file",
    "fs.write_file",
    "fs.edit_file",
    "fs.insert_file",
    "fs.read_image",
]
CAPABILITIES = (
    "bash.execute",
    "bash.get",
    "fs.read_file",
    "fs.write_file",
    "fs.edit_file",
    "fs.insert_file",
    "fs.read_image",
)
TERMINAL = frozenset(("succeeded", "failed"))
State = Literal["dispatching", "sent", "running", "unknown", "succeeded", "failed"]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CommandArgs(Model):
    argv: list[str] = Field(min_length=1, max_length=256)
    cwd: str = Field(min_length=1, max_length=4096)
    stdin: str | None = Field(default=None, max_length=262144)

    @field_validator("argv")
    @classmethod
    def valid_argv(cls, value):
        if any("\0" in item or len(item) > 8192 for item in value) or not value[0]:
            raise ValueError("invalid argv")
        return value


class ExecutionArgs(Model):
    mode: Literal["bash", "run", "script"] = "run"
    argv: list[str] | None = None
    command: str | None = Field(default=None, max_length=262144)
    script: str | None = Field(default=None, max_length=262144)
    cwd: str = Field(min_length=1, max_length=4096)
    stdin: str | None = Field(default=None, max_length=262144)
    timeout: float = Field(default=60.0, gt=0, le=300)
    timeout_behavior: Literal["background", "kill"] = "background"
    bytes: int = Field(default=8192, ge=0, le=131072)
    output_limit_bytes: int = Field(default=16777216, ge=1024, le=67108864)
    purpose: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_input(self):
        values = {"run": self.argv, "bash": self.command, "script": self.script}
        if values[self.mode] is None or any(
            v is not None for k, v in values.items() if k != self.mode
        ):
            raise ValueError("provide exactly the input for the execution mode")
        if self.mode == "run":
            CommandArgs(argv=self.argv, cwd=self.cwd, stdin=self.stdin)
        elif "\0" in values[self.mode]:
            raise ValueError("NUL in shell source")
        return self


class ExecutionGetArgs(Model):
    execution_id: Identifier
    bytes: int = Field(default=200, ge=0, le=131072)


class ReadArgs(Model):
    path: str = Field(min_length=1, max_length=4096)
    max_chars: int = Field(default=10000, ge=1, le=131072)
    type: Literal["chars", "lines"] = "chars"
    offset: int = Field(default=0, ge=0)
    limit: int | None = Field(default=None, ge=0)
    direction: Literal["forward", "backward"] = "forward"


class WriteArgs(Model):
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=262144)


class Edit(Model):
    oldText: str = Field(min_length=1, max_length=65536)
    newText: str = Field(max_length=65536)


class EditArgs(Model):
    path: str = Field(min_length=1, max_length=4096)
    edits: list[Edit] = Field(min_length=1, max_length=64)
    dry_run: bool = False
    include_diff: bool = False


class InsertArgs(Model):
    path: str = Field(min_length=1, max_length=4096)
    text: str = Field(max_length=262144)
    line: int | None = Field(default=None, ge=1)
    before: str | None = Field(default=None, min_length=1, max_length=65536)
    after: str | None = Field(default=None, min_length=1, max_length=65536)
    at_end: bool = False
    dry_run: bool = False
    include_diff: bool = False

    @model_validator(mode="after")
    def one_position(self):
        if (
            sum(value is not None for value in (self.line, self.before, self.after))
            + int(self.at_end)
            != 1
        ):
            raise ValueError("exactly one insertion position is required")
        return self


class ReadImageArgs(Model):
    path: str = Field(min_length=1, max_length=4096)
    max_bytes: int = Field(default=MAX_IMAGE_BYTES, ge=1, le=MAX_IMAGE_BYTES)


class ImagePayload(Model):
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    base64: str = Field(max_length=4 * ((MAX_IMAGE_BYTES + 2) // 3))
    size: int = Field(ge=1, le=MAX_IMAGE_BYTES)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    animated: bool


ARGUMENTS = dict(
    zip(
        CAPABILITIES,
        (
            ExecutionArgs,
            ExecutionGetArgs,
            ReadArgs,
            WriteArgs,
            EditArgs,
            InsertArgs,
            ReadImageArgs,
        ),
        strict=True,
    )
)
# New optional defaults must not change the digest of already-journaled requests.
EXTENSION_DEFAULTS = {
    "fs.read_file": {"type": "chars", "offset": 0, "limit": None, "direction": "forward"},
    "fs.edit_file": {"dry_run": False, "include_diff": False},
}


class Operation(Model):
    operation_id: Identifier
    device_id: Identifier
    action: Action
    arguments: dict

    @model_validator(mode="after")
    def normalized(self):
        args = ARGUMENTS[self.action].model_validate(self.arguments).model_dump()
        for key, default in EXTENSION_DEFAULTS.get(self.action, {}).items():
            if args[key] == default:
                del args[key]
        path = args.get("cwd", args.get("path"))
        if path is not None and (not path.startswith("/") or "\0" in path):
            raise ValueError("device paths must be absolute POSIX paths")
        self.arguments = args
        if len(canonical(self.model_dump()).encode()) > MAX_OPERATION_BYTES:
            raise ValueError("operation payload too large")
        return self

    @property
    def digest(self):
        return hashlib.sha256(canonical(self.model_dump()).encode()).hexdigest()


class Hello(Model):
    v: Literal[1]
    type: Literal["hello"]
    capabilities: list[Action]
    platform: Literal["darwin", "linux"]


class Report(Model):
    v: Literal[1]
    type: Literal["report"]
    operation_id: Identifier
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["running", "unknown", "succeeded", "failed"]
    result: dict | None = None

    @model_validator(mode="after")
    def bounded(self):
        if (self.state in TERMINAL) != (self.result is not None):
            raise ValueError("only terminal reports contain results")
        if len(canonical(self.model_dump()).encode()) > MAX_MESSAGE_BYTES:
            raise ValueError("report too large")
        return self


class Execute(Model):
    v: Literal[1]
    type: Literal["execute"]
    operation: Operation


class Ack(Model):
    v: Literal[1]
    type: Literal["ack"]
    operation_id: Identifier
    state: Literal["running", "unknown", "succeeded", "failed"]
