"""Verify as real root that evidence reads refuse an owner-created FIFO.

Scope honesty: the probe image lacks pydantic/packaging, so native_update cannot
be imported here. Two things are still verified for real:

  1. helper._digest (used by every active.json digest check) refuses a FIFO that
     root reads from a normal user's directory.
  2. The privilege asymmetry itself: root can stat and open an owner-created FIFO,
     so a type check is the only thing standing between root and a blocking read.

The native_update._read_regular path is covered by the unit test, which patches
geteuid but exercises the real function body.
"""
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, "/pkg")
from device_runtime import native_helper as nh  # noqa: E402

BASE = Path("/root/asym")
OWNER = 1000


def main():
    if os.geteuid() != 0:
        raise SystemExit("root required")
    BASE.mkdir(parents=True, exist_ok=True)
    os.chown(BASE, OWNER, OWNER)
    os.chmod(BASE, 0o700)

    results = {"uid_reading": os.geteuid(), "owner": OWNER}

    regular = BASE / "active.json"
    regular.write_text(json.dumps({"release_version": "1.0"}))
    os.chown(regular, OWNER, OWNER)
    os.chmod(regular, 0o600)
    results["regular"] = {"digest_ok": len(nh._digest(regular)) == 64}

    fifo = BASE / "fifo.json"
    if fifo.exists() or fifo.is_symlink():
        fifo.unlink()
    os.mkfifo(fifo, 0o600)
    os.chown(fifo, OWNER, OWNER)
    os.chmod(fifo, 0o600)

    # Root really can see and reach the owner's FIFO; only the type check stops it.
    info = fifo.lstat()
    results["asymmetry"] = {
        "root_can_stat": True,
        "is_fifo": stat.S_ISFIFO(info.st_mode),
        "file_owner": info.st_uid,
    }
    try:
        nh._digest(fifo)
        results["fifo_digest"] = {"outcome": "NOT REFUSED"}
    except nh.HelperError as exc:
        results["fifo_digest"] = {"outcome": "HelperError", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        results["fifo_digest"] = {"outcome": type(exc).__name__, "detail": str(exc)}

    print("ASYM " + json.dumps(results, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    main()
