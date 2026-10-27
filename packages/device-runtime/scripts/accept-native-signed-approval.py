"""Disposable administrator-side authenticated prepare/approve acceptance.

Requires root-owned package, signed envelope and public key map, plus an already
prepared ordinary-owner runtime receipt. Does not apply packages or run services.
Usage: python accept-native-signed-approval.py --registry ... --package ...
  --envelope ... --keys ... --runtime-receipt ... --release-id ...
  --instance-id ... --operation-id ... --bootstrap-version ...
"""

import argparse
import json
from pathlib import Path

from device_runtime.native_helper import _digest, _trusted
from device_runtime.native_update import Updates

parser = argparse.ArgumentParser(description=__doc__)
for name in (
    "registry",
    "package",
    "envelope",
    "keys",
    "runtime-receipt",
    "release-id",
    "instance-id",
    "operation-id",
    "bootstrap-version",
):
    parser.add_argument("--" + name, required=True)
args = parser.parse_args()
_trusted(Path(args.keys))
manager = Updates(args.registry)
result = manager.prepare_signed(
    args.instance_id,
    args.operation_id,
    args.package,
    envelope_path=args.envelope,
    trusted_keys=json.loads(Path(args.keys).read_text()),
    release_id=args.release_id,
    runtime_receipt=args.runtime_receipt,
    bootstrap_version=args.bootstrap_version,
)
stem = (
    Path(args.registry)
    / "updates"
    / f"{len(args.instance_id)}-{args.instance_id}--{args.operation_id}"
)
prepared = stem.with_suffix(".prepared.json")
approved = manager.approve(args.instance_id, args.operation_id, prepared_sha256=_digest(prepared))
assert result["authenticated_candidate"] and approved["approved_by_uid"] == 0
assert not stem.with_suffix(".apply-intent.json").exists()
print(json.dumps({"signed_prepared": True, "approved": True, "package_applied": False}))
