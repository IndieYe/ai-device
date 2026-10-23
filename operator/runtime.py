"""Private operator entry contract; standard library only, no installation side effects."""

import hashlib
import json
import os
import re
import stat
import subprocess

CENTER = "https://device.kongkongye.com"


def read_json(path, private=False):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("regular file required")
    if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError("private file required")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise TypeError("object required")
    return value


def resolve(root, role):
    active = read_json(root / "active-release.json", private=True)
    release_id = active["release_id"]
    if active["schema_version"] != 1 or not isinstance(release_id, str):
        raise ValueError("invalid binding")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", release_id):
        raise ValueError("invalid release id")
    release = root / "releases" / release_id
    if release.is_symlink() or not release.is_dir():
        raise ValueError("release unavailable")
    manifest_path = release / "release.json"
    manifest = read_json(manifest_path)
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if digest != active["manifest_sha256"]:
        raise ValueError("manifest mismatch")
    if manifest["schema_version"] != 1 or manifest["release_id"] != release_id:
        raise ValueError("manifest identity mismatch")
    item = manifest["roles"][role]
    if item["python"] != f"{role}/venv/bin/python":
        raise ValueError("invalid role executable")
    python = release / item["python"]
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("interpreter unavailable")
    return python


# Parse only; do not call run_operations or import client execution paths.
OPS_PARSE = """import contextlib,io,json,sys
from device_runtime.operations_cli import OperationsParser,add_operations_parser
result={"kind":"invalid"}
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
    parser=OperationsParser(prog="device-ops")
    modes=parser.add_subparsers(dest="mode",required=True,parser_class=OperationsParser)
    add_operations_parser(modes)
    # Detect even an explicit override equal to the normal default.
    modes.choices["ops"].set_defaults(token_env=None)
    try:
        parsed=parser.parse_args(["ops",*json.load(sys.stdin)])
        result={"kind":"operation","override":parsed.server_url is not None or parsed.token_env is not None}
    except SystemExit as error:
        result={"kind":"help" if error.code == 0 else "invalid"}
print(json.dumps(result))
"""


def classify_ops(python, args, env):
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", OPS_PARSE],
        input=json.dumps(args),
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    value = json.loads(result.stdout)
    if value.get("kind") not in {"help", "operation"}:
        raise ValueError("invalid arguments")
    if value.get("override"):
        raise ValueError("profile_connection_is_pinned")
    return value["kind"] == "help"


def run(root, role, args):
    """Resolve both roles from one binding. Never fall back to candidates."""
    if role == "mcp" and args:
        raise ValueError("unexpected MCP arguments")
    python = resolve(root, role)
    env = os.environ.copy()
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "DEVICE_RUNTIME_ADMIN_TOKEN",
        "DEVICE_RUNTIME_DEVICE_TOKENS",
        "DEVICE_RUNTIME_TOKEN",
        "DEVICE_RUNTIME_API_URL",
    ):
        env.pop(key, None)
    help_only = role == "ops" and (not args or classify_ops(python, args, env))
    if not help_only:
        config = read_json(root / "config.json", private=True)
        token = read_json(root / "credentials.json", private=True)["admin_token"]
        if (
            config["server_url"] != CENTER
            or not isinstance(token, str)
            or len(token) < 16
        ):
            raise ValueError("private profile unavailable")
        env["DEVICE_RUNTIME_API_URL"] = CENTER
        env["DEVICE_RUNTIME_ADMIN_TOKEN"] = token
    module_args = (
        ["device_runtime.mcp_server"]
        if role == "mcp"
        else ["device_runtime.cli", "ops", *args]
    )
    os.execve(str(python), [str(python), "-I", "-m", *module_args], env)


def main(root, role, args):
    try:
        run(root, role, args)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # Do not expose profile, credentials, input payloads or exception strings.
        print(
            json.dumps(
                {"ok": False, "code": "operator_entry_unavailable", "data": None}
            ),
            file=__import__("sys").stderr,
        )
        return 2
    return 0
