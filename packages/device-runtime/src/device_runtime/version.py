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


# Native executables this tree ships, relative to the tree root. Their bytes are
# part of the program identity: the launcher and the commit helper are compiled
# from repository sources by the build and replaced together with the Python
# package, so a change to either must change build_id. The upgrade entry is NOT
# here -- it lives outside the tree and is not part of what a running client is.
NATIVE_IDENTITY_FILES = ("bin/ai-device-client-launcher", "bin/ai-device-client-commit")


def _source_fingerprint():
    # Hash names and exact bytes, not timestamps, venv paths or credentials.
    # This is a source fingerprint, NOT a wheel digest or proof of integrity.
    #
    # Two inputs, in this order:
    #   1. every .py in this package, so a Python-only change moves the digest;
    #   2. every native executable the tree ships, so a native-only change does.
    #   3. the tree-relative marker for each native binary, so renaming or
    #      dropping one is a change even if the remaining bytes are identical.
    #
    # The native binaries are found by walking up from this package to the tree
    # root. That distance is fixed by the install layout; if the layout ever
    # changes, the binaries stop being found and the digest changes, which is the
    # safe direction -- a silently ignored native change is not.
    #
    # build-client-tree.py computes the same digest for client-identity.json, and
    # readiness compares the two exactly, so the two implementations must stay
    # in step. tests/test_client_tree_build.py pins them against each other.
    root = Path(__file__).parent
    try:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*.py")):
            name, content = path.relative_to(root).as_posix().encode(), path.read_bytes()
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(len(content).to_bytes(8, "big") + content)
        tree = root.parents[4] if len(root.parents) > 4 else None
        for relative in NATIVE_IDENTITY_FILES:
            marker = relative.encode()
            digest.update(len(marker).to_bytes(8, "big") + marker)
            binary = (tree / relative) if tree else None
            content = b""
            if binary is not None:
                try:
                    content = binary.read_bytes()
                except FileNotFoundError:
                    # The name is hashed whether or not the file exists, so a
                    # missing binary cannot equal a present one. Its bytes are
                    # simply empty here; the marker is what keeps the two states
                    # distinguishable.
                    content = b""
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
