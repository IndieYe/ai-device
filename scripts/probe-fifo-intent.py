"""Reproduce: a FIFO at the job-intent path makes the worker hang at start.

_record_run_start checks `helper._trusted(intent)`, which rejects symlinks but
not FIFOs, then calls `_digest`, which does `read_bytes()`. A root-owned FIFO
with no writer therefore blocks forever instead of failing closed.

The worker runs this before any package work, so the hang is the whole job.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/pkg")
from device_runtime import native_update_job as job  # noqa: E402

ROOT = Path("/root/fifo/manager")


def make(directory, name, *, fifo=False):
    updates = ROOT / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    path = updates / name
    if fifo:
        if path.exists() or path.is_symlink():
            path.unlink()
        os.mkfifo(path, 0o644)
        os.chmod(path, 0o644)
    else:
        path.write_text(json.dumps({"probe": True}))
        os.chmod(path, 0o644)
    return path


def run_in_child(stem_name, fifo, timeout=5):
    """Run _record_run_start in a child so a hang is observable, not fatal."""
    pid = os.fork()
    if pid == 0:
        try:
            job._record_run_start(str(ROOT), "probe", stem_name, "a" * 64)
        except job.JobError as exc:
            sys.stderr.write("JOBERROR:" + str(exc) + "\n")
            os._exit(21)
        except BaseException as exc:  # noqa: BLE001 - reporting the type is the point
            sys.stderr.write("OTHER:" + type(exc).__name__ + ":" + str(exc) + "\n")
            os._exit(22)
        os._exit(0)
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return {"exit": os.waitstatus_to_exitcode(status)}
        time.sleep(0.05)
    os.kill(pid, 9)
    os.waitpid(pid, 0)
    return {"exit": None, "blocked_seconds": timeout}


def main():
    if os.geteuid() != 0:
        raise SystemExit("root required")
    ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(ROOT, 0o700)
    (ROOT / "updates").mkdir(exist_ok=True)
    os.chmod(ROOT / "updates", 0o700)

    results = {}
    # Control: regular file with the wrong digest must refuse promptly.
    make(None, "5-probe--regular.job-intent.json")
    results["regular_file_wrong_digest"] = run_in_child("regular", fifo=False)
    # The defect: same trust checks pass, but reading blocks.
    make(None, "5-probe--fifo.job-intent.json", fifo=True)
    results["fifo_intent"] = run_in_child("fifo", fifo=True)
    print("FIFO_RESULTS " + json.dumps(results, indent=2, sort_keys=True), flush=True)
    print("PROBE_OK", flush=True)


if __name__ == "__main__":
    main()
