"""Disposable Linux preparation failure/retirement acceptance, never services.

Run fail as the registered owner, retire via the administrator CLI, then retry
as that owner, with the same arguments. Requires a pre-registered disposable
instance, a development signed package/envelope and persistent Python/uv.
The fail phase replaces only this process's preparation callback with exit(7).
No package installation, activation, cleanup or service action is performed.
"""

import argparse
import json
import sys
from pathlib import Path

from device_runtime import native_prepare as preparation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("fail", "retry"))
    for name in ("root", "package", "envelope", "keys", "release-id", "uv"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--bootstrap-version", default="1")
    args = parser.parse_args()
    root = Path(args.root)
    active = (root / "active.json").read_bytes()
    options = {
        "trusted_keys": json.loads(Path(args.keys).read_bytes()),
        "release_id": args.release_id,
        "bootstrap_version": args.bootstrap_version,
        "uv_executable": args.uv,
    }

    def prepare(operation_id):
        return preparation.prepare_runtime_candidate(
            root, args.package, args.envelope, operation_id=operation_id, **options
        )

    if args.phase == "fail":

        def fail(*a, **kw):
            preparation.core._invoke(
                [sys.executable, "-c", "raise SystemExit(7)"],
                cwd=root,
                log=root / "retirement-acceptance.log",
            )

        preparation.core._prepare = fail
        try:
            prepare("failed-tree")
        except preparation.core.InstallationError:
            pass
        else:
            raise AssertionError("expected command failure")
        stem = root / "native-candidates/failed-tree"
        request = json.loads(stem.with_suffix(".intent.json").read_bytes())
        result = json.loads(stem.with_suffix(".result.json").read_bytes())
        observed = preparation.observe_senders(stem.with_suffix(".senders"), request)
        assert result["state"] == "failed" and result["request"] == request
        assert observed["state"] == "process_trees_reaped"
        assert observed["descendants_confirmed"] and not observed["retry_allowed"]
        print("failed-tree has complete tree evidence; administrator retirement still required")
    else:
        # The old ID must not replay even after the administrator retired it.
        try:
            prepare("failed-tree")
        except preparation.PreparationError:
            pass
        else:
            raise AssertionError("old ID unexpectedly replayed")
        result = prepare("after-retirement")
        assert result["state"] == "runtime_prepared"
        assert prepare("after-retirement") == result
        assert (root / "native-candidates/failed-tree.result.json").exists()
        assert (root / "native-candidates/failed-tree.retired.json").exists()
        print("new ID prepared and stable; old failure evidence retained")
    assert (root / "active.json").read_bytes() == active
    print("active unchanged; no activation or service action")


if __name__ == "__main__":
    main()
