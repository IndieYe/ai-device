"""Copy a disposable root-owned CPython runtime and probe its relocated prefix.

No package install, service calls or cleanup. Use only a separate manager-owned
transaction directory outside package ownership; caller holds the transaction
lease. This validates snapshot mechanics, not package transaction integration.
"""

import argparse
import json
import subprocess
from pathlib import Path

from device_runtime.native_bootstrap import snapshot_runtime, validate_snapshot

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", required=True)
parser.add_argument("--destination", required=True)
args = parser.parse_args()
source, destination = Path(args.source), Path(args.destination)
first = snapshot_runtime(source, destination)
assert snapshot_runtime(source, destination) == first
assert validate_snapshot(destination) == first
python = destination / "runtime/bin/python3.13"
assert python.stat().st_ino != (source / "bin/python3.13").stat().st_ino
assert python.stat().st_nlink == 1
result = subprocess.run(
    [
        str(python),
        "-I",
        "-B",
        "-c",
        "import json,sys,ssl,sqlite3,ctypes; print(json.dumps({'prefix':sys.prefix,'executable':sys.executable}))",
    ],
    check=True,
    capture_output=True,
    text=True,
    timeout=30,
    env={"PATH": "/usr/bin:/bin", "LANG": "C"},
)
probe = json.loads(result.stdout)
assert probe["prefix"] == str(destination / "runtime")
assert probe["executable"] == str(python)
print(
    json.dumps(
        {
            "state": first["state"],
            "members": len(first["inventory"]),
            "independent_inode": True,
            "same_id_stable": True,
            "probe": probe,
        }
    )
)
