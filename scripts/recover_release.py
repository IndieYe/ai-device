"""Fixed recovery of run 39883225536; validates/downloads only, never uploads."""

import argparse
import hashlib
import io
import json
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from release_artifacts import check_files, check_public

SOURCE = "718385bc9d3514a700a6e5f042c813d4e37704dc"
RUN_ID = 39883225536
ARTIFACT_ID = 12471078590
ZIP_SHA256 = "9d798fa5a147121faebf04e079db7cbcc4cf4d2b2e7b14ee6ab07852640fb6b5"
FILES = {
    "bash/ai_device_bash-0.2.0-py3-none-any.whl": "d6b3d9fbd8856361c8be33c7d6cc6f533695be3afd959c2a768b0727f2d5596e",
    "bash/ai_device_bash-0.2.0.tar.gz": "7dac60c6db4a7d6adea4bd6c0bc710726c2b081cf263085a19c92289853d2a05",
    "fs/ai_device_fs-0.2.0-py3-none-any.whl": "b872aa775eaacc9836c0ec94a2abf1e41c8395ef725eecb37d05b521053ab626",
    "fs/ai_device_fs-0.2.0.tar.gz": "639df34658df64df1719c4b43caf4eb8fbe4519a77e1de5ee67e39ff845097f8",
    "device/ai_device-0.2.2-py3-none-any.whl": "c5e523318a13717e8988c0c740ff123f099ab1c3fadfd273d3205a8c45348bdc",
    "device/ai_device-0.2.2.tar.gz": "78d2980f8a25090735d428c7322782fe21bdb192f7c21b6df8fe0764d2047bbf",
}
REPORTS = {
    "artifact-verification.json": "bd24b51ba36933531a2e6b526cba87aefab68739d90acdef45fa4d386604158a",
    "delivery-verification.json": "02f3ee6cdb907b00493419a844152f50c366de5a5ba625cd3cfe4eb11f0231cc",
}


def validate(target):
    if (target / "source-commit.txt").read_text().strip() != SOURCE:
        raise ValueError("candidate source commit mismatch")
    if check_files(target) != FILES:
        raise ValueError("candidate manifest differs from frozen release")
    for name, digest in REPORTS.items():
        if hashlib.sha256((target / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"candidate report mismatch: {name}")


def extract(archive, target):
    raw = archive.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ZIP_SHA256:
        raise ValueError("original artifact ZIP hash mismatch")
    if target.exists():
        raise ValueError("recovery directory must not exist")
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        names = bundle.namelist()
        expected = set(FILES) | set(REPORTS) | {"manifest.json", "source-commit.txt"}
        if set(names) != expected or len(names) != len(expected):
            raise ValueError("original artifact member set mismatch")
        # Write exact known names, never archive-supplied paths or file modes.
        for name in names:
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bundle.read(name))
    validate(target)


def absent(group):
    project, version = {
        "fs": ("ai-device-fs", "0.2.0"),
        "device": ("ai-device", "0.2.2"),
    }[group]
    try:
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/{project}/{version}/json", timeout=20
        ):
            pass
    except urllib.error.HTTPError as error:
        if error.code == 404:
            print(f"Confirmed absent: {project} {version}")
            return
        raise
    raise ValueError(
        f"{project} {version} already exists: stop and reconcile; never retry upload"
    )


def preflight(target, phase):
    validate(target)
    check_public(target, "bash")
    if phase == "device":
        check_public(target, "fs")
    else:
        absent("fs")
    absent("device")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["extract", "validate", "preflight"])
    parser.add_argument("--archive", type=Path, default=Path("candidate.zip"))
    parser.add_argument("--directory", type=Path, default=Path("release"))
    parser.add_argument(
        "--phase", choices=["prepare", "fs", "device"], default="prepare"
    )
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    import os

    if (
        not re.fullmatch(r"[0-9a-f]{40}", args.expected_commit)
        or os.environ.get("GITHUB_SHA") != args.expected_commit
    ):
        raise ValueError("recovery workflow commit mismatch")
    if args.action == "extract":
        extract(args.archive, args.directory)
    elif args.action == "validate":
        validate(args.directory)
    else:
        preflight(args.directory, args.phase)
    print(
        json.dumps(
            {
                "candidate_source": SOURCE,
                "source_run": RUN_ID,
                "artifact_id": ARTIFACT_ID,
                "workflow_commit": args.expected_commit,
                "phase": args.phase,
            }
        )
    )


if __name__ == "__main__":
    main()
