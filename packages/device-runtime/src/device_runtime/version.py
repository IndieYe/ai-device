"""Process-lifetime software identity, independent of mutable installation pointers."""

import hashlib
import json
from pathlib import Path

from pydantic import Field, ValidationError

from . import __version__
from .protocol import Model

VERSION_HEADER = "X-Device-Runtime-Version"


class RuntimeVersion(Model):
    version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9.+_-]+$")
    build_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


def _source_fingerprint():
    # Hash names and exact Python source bytes, not timestamps, venv paths or credentials.
    # This is a source fingerprint, NOT a wheel digest or proof of integrity.
    root = Path(__file__).parent
    try:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*.py")):
            name, content = path.relative_to(root).as_posix().encode(), path.read_bytes()
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(len(content).to_bytes(8, "big") + content)
        return "sha256:" + digest.hexdigest()
    except OSError:
        return None


# Capture before serving requests; never reread a mutable active/release pointer.
RUNTIME_VERSION = RuntimeVersion(version=__version__, build_id=_source_fingerprint())


def runtime_version():
    return RUNTIME_VERSION.model_dump()


def version_header():
    return json.dumps(runtime_version(), separators=(",", ":"))


def parse_version_header(value):
    # Optional diagnostics must not prevent an otherwise compatible device connection.
    if value is None or len(value) > 512:
        return None
    try:
        return RuntimeVersion.model_validate_json(value)
    except ValidationError:
        return None
