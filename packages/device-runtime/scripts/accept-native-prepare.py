"""Disposable Linux owner-side runtime preparation acceptance.

Requires an already registered test instance and installed persistent tools.
Uses the adjacent development-only ephemeral signing acceptance, never services.
"""

import argparse
import json
import runpy
import sys
from pathlib import Path

from device_runtime.native_prepare import prepare_runtime_candidate

parser = argparse.ArgumentParser(description=__doc__)
for name in ("package", "authentication", "root", "uv"):
    parser.add_argument("--" + name, required=True)
args = parser.parse_args()
sys.argv = ["accept", args.package, args.authentication, "--development-ephemeral-key"]
values = runpy.run_path(str(Path(__file__).with_name("accept-native-candidate.py")))
root = Path(args.root)
before = (root / "active.json").read_bytes()
options = {
    "trusted_keys": values["keys"],
    "release_id": values["payload"]["release_id"],
    "operation_id": "runtime-one",
    "bootstrap_version": "1",
    "uv_executable": args.uv,
}
first = prepare_runtime_candidate(root, values["args"].package, values["envelope_path"], **options)
second = prepare_runtime_candidate(root, values["args"].package, values["envelope_path"], **options)
assert first == second and (root / "active.json").read_bytes() == before
assert first["candidate"]["release"] != json.loads(before)["release"]
print(
    json.dumps(
        {
            "state": first["state"],
            "activated": first["activated"],
            "release": first["candidate"]["release"],
            "same_id_stable": True,
            "active_unchanged": True,
        }
    )
)
