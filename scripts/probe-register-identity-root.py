"""Verify registration identity checks as real root against a non-root owner.

The unit tests patch geteuid/_trusted, so they prove the guard fires but not the
privilege asymmetry. Here root really reads files owned by uid 1000.

Scope: only native_helper is exercised, so no pydantic/packaging is required.
"""
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, "/pkg")
from device_runtime import native_helper as nh  # noqa: E402

BASE = Path("/root/reg")
OWNER = 1000


def own(path, mode=None):
    os.chown(path, OWNER, OWNER)
    if mode is not None:
        os.chmod(path, mode)


def main():
    if os.geteuid() != 0:
        raise SystemExit("root required")
    BASE.mkdir(parents=True, exist_ok=True)
    own(BASE, 0o700)

    results = {"uid": os.geteuid(), "owner": OWNER}

    identity = BASE / "instance.json"
    identity.write_text(json.dumps({"instance_id": "probe"}))
    own(identity, 0o600)
    results["regular_identity"] = {
        "read_ok": json.loads(nh._read_regular(identity, "x"))["instance_id"] == "probe"
    }

    fifo = BASE / "active.json"
    if fifo.exists() or fifo.is_symlink():
        fifo.unlink()
    os.mkfifo(fifo, 0o600)
    own(fifo, 0o600)
    results["asymmetry"] = {
        "root_can_stat": True,
        "file_owner": fifo.lstat().st_uid,
        "is_fifo": stat.S_ISFIFO(fifo.lstat().st_mode),
    }
    try:
        nh._read_regular(fifo, "active pointer must be a regular file")
        results["fifo_read"] = {"outcome": "NOT REFUSED"}
    except nh.HelperError as exc:
        results["fifo_read"] = {"outcome": "HelperError", "detail": str(exc)}

    # The runner check: X_OK passes for a FIFO, so the type check is what stops it.
    runner = BASE / "run"
    if runner.exists() or runner.is_symlink():
        runner.unlink()
    os.mkfifo(runner, 0o755)
    own(runner, 0o755)
    results["runner"] = {
        "access_x_ok": os.access(runner, os.X_OK),
        "is_regular": stat.S_ISREG(runner.lstat().st_mode),
    }

    print("REG " + json.dumps(results, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    main()
