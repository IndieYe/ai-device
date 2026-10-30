"""Preparation command sender barrier. Reaping is evidence, not retry permission."""

import argparse
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

from . import native_helper as helper
from .protocol import canonical

# Explicit fragments keep both protocols aligned without text-rewriting Python.
_GUARD_HEADER = """import json,os,subprocess,sys,tempfile
from pathlib import Path
receipt=Path(sys.argv[1]); intent=json.loads(sys.argv[2]); timeout=json.loads(sys.argv[3])
"""
_GUARD_COMMAND = """try:
 result=subprocess.run(sys.argv[4:],check=False,timeout=timeout)
 code=result.returncode
except subprocess.TimeoutExpired:
 code=124
"""
_GUARD_PUBLISH = """fd,tmp=tempfile.mkstemp(prefix='.prepare-sender-',dir=receipt.parent)
with os.fdopen(fd,'w') as stream:
 json.dump(value,stream,sort_keys=True); stream.flush(); os.fsync(stream.fileno())
os.link(tmp,receipt); os.unlink(tmp)
fd=os.open(receipt.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(fd); os.close(fd)
sys.exit(code)
"""
GUARD = (
    _GUARD_HEADER
    + _GUARD_COMMAND
    + "value={'state':'direct_child_reaped','intent':intent,'returncode':code,'descendants_confirmed':False}\n"
    + _GUARD_PUBLISH
)

# Adopt before spawning, then wait for ECHILD, including detached descendants.
# A live descendant deliberately blocks completion; this does not kill the tree.
TREE_GUARD = (
    _GUARD_HEADER
    + """import ctypes
if sys.platform!='linux': raise RuntimeError('subreaper requires Linux')
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(36,1,0,0,0)!=0: raise RuntimeError('subreaper unavailable')
"""
    + _GUARD_COMMAND
    + """while True:
 try: os.waitpid(-1,0)
 except InterruptedError: continue
 except ChildProcessError: break
value={'state':'process_tree_reaped','intent':intent,'returncode':code,'descendants_confirmed':True,'mechanism':'linux-subreaper-v1'}
"""
    + _GUARD_PUBLISH
)


class PreparationSender:
    def __init__(self, directory, request, *, reap_descendants=False):
        self.directory = Path(directory)
        self.request = request
        self.reap_descendants = reap_descendants
        self.sequence = 0
        self.fd = None

    def __enter__(self):
        self.directory.mkdir(mode=0o700)
        with helper._directory_fd(self.directory.parent) as fd:
            os.fsync(fd)
        self.fd = os.open(
            self.directory / "execution.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *args):
        os.close(self.fd)
        self.fd = None

    def run(self, argv, *, cwd, env, stdout, timeout):
        if self.fd is None:
            raise ValueError("preparation sender lease required")
        self.sequence += 1
        stem = self.directory / str(self.sequence)
        intent = {
            "request": self.request,
            "sequence": self.sequence,
            "argv": [str(a) for a in argv],
            "cwd": str(cwd),
            "barrier": "linux-subreaper-v1" if self.reap_descendants else "direct-child-v1",
        }
        helper._save_new(stem.with_suffix(".intent.json"), intent)
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                TREE_GUARD if self.reap_descendants else GUARD,
                str(stem.with_suffix(".reaped.json")),
                canonical(intent),
                canonical(timeout),
                *argv,
            ],
            cwd=cwd,
            env=env,
            stdout=stdout,
            stderr=stdout,
            check=False,
            pass_fds=(self.fd,),
        )
        return result


def observe_senders(directory, request):
    """Read-only diagnostics. Neither a recovery receipt nor retry authority.

    Lock availability plus matching direct-child receipts is deliberately not
    sufficient to prove descendants terminated or the preparation is reusable.
    """
    directory = Path(directory)
    report = {"retry_allowed": False, "descendants_confirmed": False, "commands": []}
    fd = os.open(directory / "execution.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {**report, "state": "sender_busy"}
        paths = list(directory.glob("*.intent.json"))
        if len(paths) > 1000:
            raise ValueError("sender observation exceeds budget")
        sequences = []
        for path in paths:
            if path.is_symlink() or path.stat().st_size > 256000:
                raise ValueError("invalid sender intent file")
            intent = json.loads(path.read_bytes())
            sequence = intent.get("sequence")
            if (
                type(sequence) is not int
                or path.name != f"{sequence}.intent.json"
                or intent.get("request") != request
            ):
                raise ValueError("sender intent request differs")
            sequences.append(sequence)
            receipt_path = path.with_name(f"{sequence}.reaped.json")
            state = "unknown"
            if receipt_path.exists():
                if receipt_path.is_symlink() or receipt_path.stat().st_size > 256000:
                    raise ValueError("invalid sender receipt file")
                receipt = json.loads(receipt_path.read_bytes())
                full = intent.get("barrier") == "linux-subreaper-v1"
                if (
                    receipt.get("intent") != intent
                    or receipt.get("state")
                    != ("process_tree_reaped" if full else "direct_child_reaped")
                    or type(receipt.get("returncode")) is not int
                    or receipt.get("descendants_confirmed") is not full
                    or (full and receipt.get("mechanism") != "linux-subreaper-v1")
                ):
                    raise ValueError("matching sender receipt required")
                state = "process_tree_reaped" if full else "direct_child_reaped"
            report["commands"].append({"sequence": sequence, "state": state})
        if sorted(sequences) != list(range(1, len(sequences) + 1)):
            raise ValueError("sender sequence gap")
        report["commands"].sort(key=lambda item: item["sequence"])
        all_trees = bool(paths) and all(
            item["state"] == "process_tree_reaped" for item in report["commands"]
        )
        return {
            **report,
            "descendants_confirmed": all_trees,
            "state": "process_trees_reaped"
            if all_trees
            else (
                "unknown"
                if not paths or any(item["state"] == "unknown" for item in report["commands"])
                else "direct_children_reaped_descendants_unconfirmed"
            ),
        }
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(
        description="Administrator read-only native preparation sender observation; never unlocks retry"
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("administrator required")
    helper._identifier(args.operation_id)
    root = helper._path(args.root)
    helper._trusted_marker(root / helper.MARKER)
    marker = json.loads((root / helper.MARKER).read_bytes())
    if (
        marker.get("schema_version") != 2
        or marker["binding"].get("root") != str(root)
        or root.stat().st_uid != marker["binding"].get("uid")
    ):
        parser.error("matching complete registration required")
    directory = root / "native-candidates"
    intent = directory / (args.operation_id + ".intent.json")
    if intent.is_symlink() or intent.stat().st_size > 256000:
        parser.error("invalid preparation intent")
    request = json.loads(intent.read_bytes())
    print(canonical(observe_senders(directory / (args.operation_id + ".senders"), request)))


if __name__ == "__main__":
    main()
